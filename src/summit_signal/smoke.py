"""Headless MuJoCo smoke rehearsal for the pinned 29-DOF G1 arm contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from summit_signal.config import CONTROLLED_JOINTS, HardwareConfig
from summit_signal.model_contract import (
    ModelContractError,
    resolve_model_path,
    verify_official_model,
)
from summit_signal.rehearsal import scripted_goal
from summit_signal.safety import ArmSafetyController, SafetyState
from summit_signal.simulator import G1ArmSimulation, SimulationContractError


def run_smoke(
    model_path: Path,
    *,
    seconds: float = 3.0,
    hardware_config: HardwareConfig | None = None,
    hardware_config_sha256: str | None = None,
) -> dict[str, Any]:
    if not 1.0 <= seconds <= 30.0:
        raise ValueError("smoke duration must be between 1 and 30 seconds")
    scene = verify_official_model(model_path)
    target_velocity_limit = hardware_config.max_target_velocity_rad_s if hardware_config else 0.25
    measured_velocity_trip = hardware_config.max_measured_velocity_rad_s if hardware_config else 0.5
    excursion_scale = hardware_config.excursion_scale if hardware_config else 1.0
    plant = G1ArmSimulation(
        scene,
        kp=hardware_config.arm_kp if hardware_config else 20.0,
        kd=hardware_config.arm_kd if hardware_config else 1.5,
    )
    timestep = float(plant.model.opt.timestep)
    if not math.isclose(timestep, 0.002, abs_tol=1e-12):
        raise SimulationContractError(f"expected a 0.002 s model timestep, found {timestep}")

    controller = ArmSafetyController(
        watchdog_timeout_s=0.25,
        max_target_velocity_rad_s=target_velocity_limit,
        control_period_s=hardware_config.control_period_s if hardware_config else timestep,
        started_at=0.0,
        max_measured_velocity_rad_s=measured_velocity_trip,
        excursion_scale=excursion_scale,
    )
    warmup_s = min(0.3, seconds * 0.15)
    motion_end_s = max(warmup_s + 0.5, seconds - 0.5)
    now = 0.0
    next_goal_at = 0.0
    sequence = 0
    steps = 0
    goals = 0
    finite = True
    hard_limits_ok = True
    baseline: tuple[float, float, float] | None = None
    max_target_offsets = [0.0, 0.0, 0.0]
    max_actual_offsets = [0.0, 0.0, 0.0]
    max_actual_velocities = [0.0, 0.0, 0.0]
    max_target_slew = 0.0
    previous_targets: tuple[float, float, float] | None = None
    previous_enabled = False

    while now < seconds:
        if now + 1e-12 >= next_goal_at:
            goal = scripted_goal(
                sequence,
                elapsed_s=now,
                sent_monotonic_s=now,
                warmup_s=warmup_s,
                motion_end_s=motion_end_s,
            )
            controller.accept_goal(goal, received_at=now)
            sequence += 1
            goals += 1
            next_goal_at += 0.1
        positions = plant.positions()
        hard_limits_ok = hard_limits_ok and all(
            spec.hard_min_rad <= positions[spec.dds_index] <= spec.hard_max_rad
            for spec in CONTROLLED_JOINTS
        )
        command = controller.step(
            now=now,
            positions=positions,
            velocities=plant.velocities(),
        )
        if command.enabled and baseline is None:
            baseline = tuple(positions[spec.dds_index] for spec in CONTROLLED_JOINTS)
        if command.enabled and baseline is not None:
            velocities = plant.velocities()
            for index, (spec, target, center) in enumerate(
                zip(CONTROLLED_JOINTS, command.target_positions, baseline, strict=True)
            ):
                max_target_offsets[index] = max(max_target_offsets[index], abs(target - center))
                max_actual_offsets[index] = max(
                    max_actual_offsets[index], abs(positions[spec.dds_index] - center)
                )
                max_actual_velocities[index] = max(
                    max_actual_velocities[index], abs(velocities[spec.dds_index])
                )
            if previous_targets is not None and previous_enabled:
                max_target_slew = max(
                    max_target_slew,
                    max(
                        abs(current - previous) / timestep
                        for current, previous in zip(
                            command.target_positions, previous_targets, strict=True
                        )
                    ),
                )
        previous_targets = command.target_positions
        previous_enabled = command.enabled
        if command.state is SafetyState.LATCHED or not plant.step(command):
            break
        stepped_positions = plant.positions()
        hard_limits_ok = hard_limits_ok and all(
            spec.hard_min_rad <= stepped_positions[spec.dds_index] <= spec.hard_max_rad
            for spec in CONTROLLED_JOINTS
        )
        finite = finite and all(
            math.isfinite(float(value))
            for values in (plant.data.qpos, plant.data.qvel, plant.data.ctrl)
            for value in values
        )
        if not finite:
            break
        steps += 1
        now = steps * timestep

    limits_ok = all(
        max_target_offsets[index] <= spec.max_excursion_rad * excursion_scale + 1e-9
        for index, spec in enumerate(CONTROLLED_JOINTS)
    )
    safe = (
        finite
        and baseline is not None
        and limits_ok
        and hard_limits_ok
        and max_target_slew <= target_velocity_limit + 1e-6
        and controller.state is SafetyState.DISARMED
        and steps == round(seconds / timestep)
    )
    return {
        "schema_version": 1,
        "safe": safe,
        "model": {
            "actuators": int(plant.model.nu),
            "qpos": int(plant.model.nq),
            "qvel": int(plant.model.nv),
            "timestep_s": timestep,
        },
        "hardware_contract": {
            "config_sha256": hardware_config_sha256,
            "motion_profile": hardware_config.motion_profile if hardware_config else "demo",
            "excursion_scale": excursion_scale,
            "target_velocity_limit_rad_s": target_velocity_limit,
            "measured_velocity_trip_rad_s": measured_velocity_trip,
            "arm_kp": plant.kp,
            "arm_kd": plant.kd,
        },
        "rehearsal": {
            "seconds": seconds,
            "steps": steps,
            "goals": goals,
            "final_state": controller.state.value,
            "stop_reason": controller.reason,
            "finite": finite,
            "target_limits_ok": limits_ok,
            "hard_joint_limits_ok": hard_limits_ok,
            "max_target_slew_rad_s": max_target_slew,
            "measured_velocity_trip_rad_s": measured_velocity_trip,
            "max_target_offset_rad": {
                spec.name: max_target_offsets[index] for index, spec in enumerate(CONTROLLED_JOINTS)
            },
            "max_actual_offset_rad": {
                spec.name: max_actual_offsets[index] for index, spec in enumerate(CONTROLLED_JOINTS)
            },
            "max_actual_velocity_rad_s": {
                spec.name: max_actual_velocities[index]
                for index, spec in enumerate(CONTROLLED_JOINTS)
            },
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--hardware-config", type=Path)
    parser.add_argument("--seconds", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        hardware_config = None
        config_digest = None
        if args.hardware_config is not None:
            config_bytes = args.hardware_config.read_bytes()
            config_digest = hashlib.sha256(config_bytes).hexdigest()
            hardware_config = HardwareConfig.load(args.hardware_config, require_activation=False)
        result = run_smoke(
            resolve_model_path(args.model),
            seconds=args.seconds,
            hardware_config=hardware_config,
            hardware_config_sha256=config_digest,
        )
    except (ModelContractError, SimulationContractError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["safe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
