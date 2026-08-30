"""Lazy Unitree low-level adapter with facility-mandated all-joint damping."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from summit_signal.config import CONTROLLED_JOINTS, HardwareConfig
from summit_signal.safety import ArmCommand, SafetyState

G1_MOTOR_COUNT = 29


class UnitreeAdapterError(RuntimeError):
    """Raised when the official SDK or live robot state fails its contract."""


@dataclass(frozen=True)
class RobotState:
    positions: tuple[float, ...]
    velocities: tuple[float, ...]
    mode_machine: int
    mode_pr: int
    received_at: float


class UnitreeLowLevelAdapter:
    """Publish 29-joint low-level frames only after explicit ``connect()``."""

    def __init__(
        self,
        config: HardwareConfig,
        network_interface: str | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if network_interface is not None and (
            not network_interface or any(character.isspace() for character in network_interface)
        ):
            raise UnitreeAdapterError("network interface must be a non-empty interface name")
        self.config = config
        self.network_interface = network_interface
        self._clock = clock
        self._lock = threading.Lock()
        self._state: RobotState | None = None
        self._publisher: Any = None
        self._subscriber: Any = None
        self._message: Any = None
        self._crc: Any = None
        self._connected = False
        self._gain_scale = 0.0
        self._last_write_at: float | None = None

    def connect(self) -> None:
        """Initialize DDS lazily; this is the first robot-I/O operation."""

        if self._connected:
            raise UnitreeAdapterError("adapter is already connected")
        try:
            from unitree_sdk2py.core.channel import (
                ChannelFactoryInitialize,
                ChannelPublisher,
                ChannelSubscriber,
            )
            from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_, LowState_
            from unitree_sdk2py.utils.crc import CRC
        except ImportError as error:
            raise UnitreeAdapterError(
                "unitree_sdk2py is unavailable; use the facility-provided Jetson environment"
            ) from error

        if self.network_interface is None:
            ChannelFactoryInitialize(0)
        else:
            ChannelFactoryInitialize(0, self.network_interface)
        publisher = ChannelPublisher(self.config.command_topic, LowCmd_)
        publisher.Init()
        subscriber = ChannelSubscriber(self.config.state_topic, LowState_)
        subscriber.Init(self._on_state, 1)
        self._message = unitree_hg_msg_dds__LowCmd_()
        self._crc = CRC()
        self._publisher = publisher
        self._subscriber = subscriber
        self._connected = True

    def _on_state(self, message: Any) -> None:
        try:
            motor_state = message.motor_state
            if len(motor_state) < G1_MOTOR_COUNT:
                return
            positions = tuple(float(motor_state[index].q) for index in range(G1_MOTOR_COUNT))
            velocities = tuple(float(motor_state[index].dq) for index in range(G1_MOTOR_COUNT))
            if not all(math.isfinite(value) for value in (*positions, *velocities)):
                return
            state = RobotState(
                positions=positions,
                velocities=velocities,
                mode_machine=int(message.mode_machine),
                mode_pr=int(message.mode_pr),
                received_at=self._clock(),
            )
        except (AttributeError, IndexError, TypeError, ValueError):
            return
        with self._lock:
            self._state = state

    def latest_state(self, *, now: float | None = None) -> RobotState:
        timestamp = self._clock() if now is None else now
        state = self._state_snapshot()
        if not math.isfinite(timestamp) or timestamp < state.received_at:
            raise UnitreeAdapterError("low-state clock is invalid")
        if timestamp - state.received_at >= self.config.low_state_timeout_s:
            raise UnitreeAdapterError("low-state watchdog expired")
        return state

    def _state_snapshot(self) -> RobotState:
        with self._lock:
            state = self._state
        if state is None:
            raise UnitreeAdapterError("no valid 29-DOF low-state sample received")
        return state

    def wait_for_state(self, timeout_s: float) -> RobotState:
        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            try:
                return self.latest_state()
            except UnitreeAdapterError:
                time.sleep(0.01)
        raise UnitreeAdapterError("timed out waiting for valid G1 low-state data")

    @staticmethod
    def _controlled_target_map(command: ArmCommand) -> dict[int, float]:
        if command.enabled and command.state is not SafetyState.ACTIVE:
            raise UnitreeAdapterError("enabled arm command must be ACTIVE")
        targets: dict[int, float] = {}
        for spec, target in zip(CONTROLLED_JOINTS, command.target_positions, strict=True):
            if not math.isfinite(target) or not spec.hard_min_rad <= target <= spec.hard_max_rad:
                raise UnitreeAdapterError(f"unsafe target for {spec.name}")
            targets[spec.dds_index] = target
        return targets

    def _next_gain_scale(self, enabled: bool, now: float) -> float:
        if not math.isfinite(now) or (
            self._last_write_at is not None and now < self._last_write_at
        ):
            raise UnitreeAdapterError("control clock is invalid")
        if not enabled:
            self._gain_scale = 0.0
        else:
            dt = (
                self.config.control_period_s
                if self._last_write_at is None
                else min(max(now - self._last_write_at, 0.0), self.config.watchdog_timeout_s)
            )
            self._gain_scale = min(
                1.0,
                self._gain_scale + dt / self.config.enable_gain_ramp_s,
            )
        self._last_write_at = now
        return self._gain_scale

    def _write_frame(
        self,
        state: RobotState,
        *,
        controlled: dict[int, float] | None,
        gain_scale: float,
    ) -> None:
        assert self._message is not None and self._publisher is not None and self._crc is not None
        message = self._message
        message.mode_machine = state.mode_machine
        message.mode_pr = state.mode_pr
        for index in range(G1_MOTOR_COUNT):
            active = controlled is not None and index in controlled
            motor = message.motor_cmd[index]
            motor.mode = 1
            motor.tau = 0.0
            motor.q = float(controlled[index] if active else state.positions[index])
            motor.dq = 0.0
            motor.kp = self.config.arm_kp * gain_scale if active else 0.0
            motor.kd = (
                self.config.all_joint_damping_kd
                + gain_scale * (self.config.arm_kd - self.config.all_joint_damping_kd)
                if active
                else self.config.all_joint_damping_kd
            )
        message.crc = self._crc.Crc(message)
        self._publisher.Write(message)

    def publish(self, command: ArmCommand, *, now: float | None = None) -> float:
        if not self._connected or self._publisher is None or self._message is None:
            raise UnitreeAdapterError("adapter is not connected")
        timestamp = self._clock() if now is None else now
        state = self.latest_state(now=timestamp)
        controlled = self._controlled_target_map(command)
        gain_scale = self._next_gain_scale(command.enabled, timestamp)
        self._write_frame(
            state,
            controlled=controlled if command.enabled else None,
            gain_scale=gain_scale,
        )
        return gain_scale

    def release(self) -> None:
        """Write all-29-joint damping frames before the process exits."""

        if not self._connected or self._publisher is None or self._message is None:
            return
        with self._lock:
            state = self._state
        if state is None:
            # kp=0 makes q dynamically irrelevant. Using zero here lets the
            # shutdown contract still be honored if DDS initialized but no
            # valid low-state sample ever arrived.
            state = RobotState(
                positions=(0.0,) * G1_MOTOR_COUNT,
                velocities=(0.0,) * G1_MOTOR_COUNT,
                mode_machine=int(getattr(self._message, "mode_machine", 0)),
                mode_pr=int(getattr(self._message, "mode_pr", 0)),
                received_at=self._clock(),
            )
        duration = self.config.shutdown_damping_duration_s
        steps = max(1, round(duration * self.config.control_hz))
        self._gain_scale = 0.0
        for step in range(steps):
            # The sample may be stale because state loss can trigger this path.
            self._write_frame(state, controlled=None, gain_scale=0.0)
            if step + 1 < steps:
                time.sleep(self.config.control_period_s)


def validate_state_vector(positions: Sequence[float]) -> tuple[float, ...]:
    """Public pure validator used by dry tests and recorded-state replay."""

    if len(positions) < G1_MOTOR_COUNT:
        raise UnitreeAdapterError("expected at least 29 motor positions")
    values = tuple(float(value) for value in positions[:G1_MOTOR_COUNT])
    if not all(math.isfinite(value) for value in values):
        raise UnitreeAdapterError("motor positions must be finite")
    return values
