"""Jetson process: local 500 Hz low-level loop with watchdog and hardware gates."""

from __future__ import annotations

import argparse
import math
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from summit_signal.config import CONTROLLED_JOINTS, ConfigError, HardwareConfig
from summit_signal.protocol import MAX_GOAL_BYTES, ArmGoal, ProtocolError
from summit_signal.safety import ArmSafetyController
from summit_signal.unitree_adapter import UnitreeAdapterError, UnitreeLowLevelAdapter

STATUS_PERIOD_S = 0.2


@dataclass(frozen=True)
class ReceivedGoal:
    goal: ArmGoal | None
    received_at: float
    error: str | None = None
    eof: bool = False


class GoalReceiver:
    """Read bounded stdin lines on a worker and retain only the newest goal."""

    def __init__(self, stream: BinaryIO, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._stream = stream
        self._clock = clock
        # One latest goal plus one terminal EOF/error marker. This prevents a
        # cleanly sent final stop goal from being overwritten by immediate EOF.
        self._queue: queue.Queue[ReceivedGoal] = queue.Queue(maxsize=2)
        self._thread = threading.Thread(target=self._run, name="goal-receiver", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _put_latest(self, item: ReceivedGoal) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with suppress(queue.Empty):
                self._queue.get_nowait()
            self._queue.put_nowait(item)

    def _run(self) -> None:
        while True:
            line = self._stream.readline(MAX_GOAL_BYTES + 1)
            received_at = self._clock()
            if not line:
                self._put_latest(ReceivedGoal(None, received_at, eof=True))
                return
            if len(line) > MAX_GOAL_BYTES or not line.endswith(b"\n"):
                self._put_latest(ReceivedGoal(None, received_at, error="oversized goal line"))
                return
            try:
                goal = ArmGoal.decode(line)
            except ProtocolError as error:
                self._put_latest(ReceivedGoal(None, received_at, error=str(error)))
                return
            self._put_latest(ReceivedGoal(goal, received_at))

    def take(self, timeout: float | None = None) -> ReceivedGoal | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None


# Terminating the process is the only remote stop once the built-in motion service is
# released, so every catchable termination signal must reach the damping shutdown.
# SIGHUP matters most: its default action kills the process outright, which would skip
# the release() damping write and leave the last commanded frame latched on the robot.
STOP_SIGNALS: tuple[int, ...] = tuple(
    sig
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None))
    if sig is not None
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--motion-profile", choices=("commissioning", "demo"), required=True)
    parser.add_argument("--real-robot", action="store_true")
    parser.add_argument("--facility-rules-acknowledged", action="store_true")
    parser.add_argument("--exclusive-access-confirmed", action="store_true")
    parser.add_argument("--camera-confirmed", action="store_true")
    parser.add_argument("--developer-mode-confirmed", action="store_true")
    return parser


def validate_activation(args: argparse.Namespace, config: HardwareConfig) -> None:
    if not args.real_robot:
        raise ConfigError("refusing hardware I/O without --real-robot")
    confirmations = {
        "--facility-rules-acknowledged": args.facility_rules_acknowledged,
        "--exclusive-access-confirmed": args.exclusive_access_confirmed,
        "--camera-confirmed": args.camera_confirmed,
        "--developer-mode-confirmed": args.developer_mode_confirmed,
    }
    missing = [flag for flag, confirmed in confirmations.items() if not confirmed]
    if missing:
        raise ConfigError(f"refusing hardware I/O without {' '.join(missing)}")
    if not config.hardware_enabled or not config.facility_rules_reference:
        raise ConfigError("real-robot activation is not enabled in the hardware configuration")
    if args.motion_profile != config.motion_profile:
        raise ConfigError("--motion-profile must match the activated hardware configuration")
    if sys.stdin.isatty():
        raise ConfigError("stdin must be a live operator goal stream, not an interactive terminal")


def _consume(receiver: GoalReceiver, controller: ArmSafetyController) -> None:
    while (received := receiver.take()) is not None:
        if received.error is not None:
            controller.latch(f"invalid operator stream: {received.error}")
            return
        if received.eof:
            controller.latch("operator stream closed")
            return
        assert received.goal is not None
        controller.accept_goal(received.goal, received_at=received.received_at)


def _validated_initial_goal(received: ReceivedGoal | None) -> ReceivedGoal:
    if received is None:
        raise ConfigError("initial operator goal timed out before DDS initialization")
    if received.error is not None or received.eof or received.goal is None:
        raise ConfigError(received.error or "operator stream closed before DDS initialization")
    goal = received.goal
    if goal.deadman or goal.emergency_stop or goal.quit or not goal.connected:
        raise ConfigError("initial operator goal must be connected, centered, and disarmed")
    if any(axis != 0.0 for axis in goal.axes):
        raise ConfigError("center both sticks before DDS initialization")
    return received


def run(
    args: argparse.Namespace,
    *,
    adapter_factory: Callable[[HardwareConfig], UnitreeLowLevelAdapter] = UnitreeLowLevelAdapter,
) -> int:
    config = HardwareConfig.load(args.config)
    validate_activation(args, config)
    receiver = GoalReceiver(sys.stdin.buffer)
    receiver.start()
    initial = _validated_initial_goal(receiver.take(timeout=config.initial_goal_timeout_s))
    adapter = adapter_factory(config)
    stop_requested = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop_requested.set()

    previous_handlers = {sig: signal.signal(sig, request_stop) for sig in STOP_SIGNALS}
    connected = False
    controller: ArmSafetyController | None = None
    release_error: Exception | None = None
    try:
        # All flags/configuration are validated before this first robot-I/O operation.
        adapter.connect()
        connected = True
        adapter.wait_for_state(config.initial_goal_timeout_s)
        controller = ArmSafetyController(
            watchdog_timeout_s=config.watchdog_timeout_s,
            max_target_velocity_rad_s=config.max_target_velocity_rad_s,
            control_period_s=config.control_period_s,
            started_at=initial.received_at,
            max_measured_velocity_rad_s=config.max_measured_velocity_rad_s,
            excursion_scale=config.excursion_scale,
        )
        assert initial.goal is not None
        controller.accept_goal(initial.goal, received_at=initial.received_at)
        _consume(receiver, controller)
        print(
            "CONNECTED: 29-motor state valid; lowcmd controller started; watchdog active.",
            file=sys.stderr,
        )
        period = config.control_period_s
        next_status_at = time.monotonic()
        while not controller.shutdown_requested and not stop_requested.is_set():
            cycle_start = time.monotonic()
            _consume(receiver, controller)
            control_now = time.monotonic()
            try:
                state = adapter.latest_state(now=control_now)
            except UnitreeAdapterError as error:
                controller.latch(str(error))
                break
            command = controller.step(
                now=control_now,
                positions=state.positions,
                velocities=state.velocities,
            )
            gain_scale = adapter.publish(command, now=control_now)
            if control_now >= next_status_at:
                measured_deg = tuple(
                    math.degrees(state.positions[spec.dds_index]) for spec in CONTROLLED_JOINTS
                )
                target_deg = tuple(math.degrees(value) for value in command.target_positions)
                print(
                    f"\r{command.state.value:<8} arm_gain={gain_scale:.3f} "
                    f"sequence_age<={config.watchdog_timeout_s:.2f}s "
                    "measured_deg=("
                    f"{', '.join(f'{value:+.2f}' for value in measured_deg)}) "
                    "target_deg=("
                    f"{', '.join(f'{value:+.2f}' for value in target_deg)})",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
                next_status_at = control_now + STATUS_PERIOD_S
            if command.shutdown_requested:
                break
            remaining = period - (time.monotonic() - cycle_start)
            if remaining > 0:
                time.sleep(remaining)
        if stop_requested.is_set():
            controller.latch("process signal received")
    finally:
        if connected:
            try:
                adapter.release()
            except Exception as error:  # The admin kill switch remains the final independent layer.
                release_error = error
                print(f"RELEASE FAILURE: {error}", file=sys.stderr)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    if release_error is not None:
        return 3
    reason = controller.reason if controller is not None else None
    print(f"\nALL-29 DAMPING SHUTDOWN COMPLETE: {reason or 'operator exit'}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        return run(parser.parse_args(argv))
    except (ConfigError, UnitreeAdapterError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
