"""Offline replay proving recorded normalized goals stay inside the safety envelope."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from summit_signal.config import CONTROLLED_JOINTS
from summit_signal.protocol import MAX_GOAL_BYTES, ArmGoal, ProtocolError
from summit_signal.safety import ArmSafetyController, SafetyState
from summit_signal.unitree_adapter import validate_state_vector

MAX_REPLAY_GOALS = 10_000


def load_initial_state(path: Path) -> tuple[float, ...]:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read initial state: {error}") from error
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "positions"}:
        raise ValueError("initial state must contain only schema_version and positions")
    if raw["schema_version"] != 1 or not isinstance(raw["positions"], list):
        raise ValueError("unsupported initial-state schema")
    return validate_state_vector(raw["positions"])


def load_goals(path: Path) -> list[ArmGoal]:
    goals: list[ArmGoal] = []
    try:
        with path.open("rb") as stream:
            for line_number, line in enumerate(stream, start=1):
                if line_number > MAX_REPLAY_GOALS:
                    raise ValueError("goal replay exceeds the 10,000-line limit")
                if len(line) > MAX_GOAL_BYTES:
                    raise ValueError(f"goal line {line_number} exceeds the wire limit")
                try:
                    goals.append(ArmGoal.decode(line))
                except ProtocolError as error:
                    raise ValueError(f"invalid goal line {line_number}: {error}") from error
    except OSError as error:
        raise ValueError(f"cannot read goal replay: {error}") from error
    if not goals:
        raise ValueError("goal replay is empty")
    return goals


def replay_goals(goals: list[ArmGoal], initial_positions: tuple[float, ...]) -> dict[str, Any]:
    positions = list(validate_state_vector(initial_positions))
    controller = ArmSafetyController(
        watchdog_timeout_s=0.25,
        max_target_velocity_rad_s=0.25,
        control_period_s=0.02,
        started_at=0.0,
    )
    now = 0.0
    max_offsets = [0.0, 0.0, 0.0]
    baseline = [positions[spec.dds_index] for spec in CONTROLLED_JOINTS]
    accepted = 0
    for goal in goals:
        accepted += int(controller.accept_goal(goal, received_at=now))
        for _ in range(5):
            now += 0.02
            command = controller.step(now=now, positions=positions)
            for index, (spec, target, center) in enumerate(
                zip(CONTROLLED_JOINTS, command.target_positions, baseline, strict=True)
            ):
                if (
                    not math.isfinite(target)
                    or not spec.hard_min_rad <= target <= spec.hard_max_rad
                ):
                    raise ValueError(f"unsafe replay target for {spec.name}")
                max_offsets[index] = max(max_offsets[index], abs(target - center))
                positions[spec.dds_index] = target
            if command.state is SafetyState.LATCHED:
                break
        if controller.state is SafetyState.LATCHED:
            break
    return {
        "schema_version": 1,
        "goals_read": len(goals),
        "goals_accepted": accepted,
        "final_state": controller.state.value,
        "stop_reason": controller.reason,
        "max_offset_rad": {
            spec.name: max_offsets[index] for index, spec in enumerate(CONTROLLED_JOINTS)
        },
        "safe": all(
            max_offsets[index] <= spec.max_excursion_rad + 1e-9
            for index, spec in enumerate(CONTROLLED_JOINTS)
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goals", type=Path, required=True)
    parser.add_argument("--initial-state", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        result = replay_goals(load_goals(args.goals), load_initial_state(args.initial_state))
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
