"""Mac operator process: DualSense input to bounded JSON goals on stdout."""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from contextlib import suppress
from pathlib import Path

from summit_signal.config import DEFAULT_OPERATOR_CONFIG, ConfigError, OperatorConfig
from summit_signal.joystick import (
    ArmJoystick,
    CalibrationProfile,
    capture_calibration,
    list_joysticks,
)
from summit_signal.protocol import ArmGoal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-config", type=Path, default=DEFAULT_OPERATOR_CONFIG)
    parser.add_argument("--joystick-index", type=int, default=0)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--list-joysticks", action="store_true")
    parser.add_argument("--calibrate", type=Path, metavar="OUTPUT.json")
    parser.add_argument("--calibration-seconds", type=float, default=6.0)
    parser.add_argument("--seconds", type=float, default=120.0)
    return parser


def _terminal_goal(session_id: str, sequence: int) -> ArmGoal:
    return ArmGoal(
        session_id=session_id,
        sequence=sequence,
        sent_monotonic_s=time.monotonic(),
        axes=(0.0, 0.0, 0.0),
        deadman=False,
        emergency_stop=True,
        quit=True,
        connected=True,
    )


def _write(goal: ArmGoal) -> None:
    sys.stdout.buffer.write(goal.encode())
    sys.stdout.buffer.flush()


def run(args: argparse.Namespace) -> int:
    config = OperatorConfig.load(args.operator_config)
    if args.list_joysticks:
        for index, name in enumerate(list_joysticks()):
            print(f"{index}: {name}")
        return 0
    if args.calibrate is not None:
        capture_calibration(
            config,
            args.calibrate,
            device_index=args.joystick_index,
            sweep_seconds=args.calibration_seconds,
        )
        print(f"saved calibration to {args.calibrate}", file=sys.stderr)
        return 0
    if args.calibration is None:
        raise ConfigError("--calibration is required for goal streaming")
    if not 1.0 <= args.seconds <= 300.0:
        raise ConfigError("--seconds must be between 1 and 300")
    calibration = CalibrationProfile.load(args.calibration, config.command_axes)
    joystick = ArmJoystick(
        config,
        device_index=args.joystick_index,
        calibration=calibration,
    )
    session_id = f"g1_{secrets.token_hex(12)}"
    sequence = 0
    deadline = time.monotonic() + args.seconds
    period = 1.0 / config.goal_hz
    print(
        "Summit Signal operator ready: hold L1 to move; R2+Circle stops; Options exits.",
        file=sys.stderr,
    )
    try:
        while time.monotonic() < deadline:
            started = time.monotonic()
            goal = joystick.sample(session_id=session_id, sequence=sequence, now=started)
            _write(goal)
            sequence += 1
            if goal.emergency_stop or goal.quit or not goal.connected:
                return 2 if not goal.connected else 0
            remaining = period - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    except (BrokenPipeError, KeyboardInterrupt):
        return 130
    finally:
        with suppress(BrokenPipeError):
            _write(_terminal_goal(session_id, sequence))
        joystick.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        return run(parser.parse_args(argv))
    except (ConfigError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
