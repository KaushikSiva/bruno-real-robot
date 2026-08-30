"""Simulation-first right-arm rehearsal using Unitree's official 29-DOF model."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from summit_signal.config import (
    CONTROLLED_JOINTS,
    DEFAULT_OPERATOR_CONFIG,
    G1_29DOF_MODEL_JOINTS,
    ConfigError,
    HardwareConfig,
    OperatorConfig,
)
from summit_signal.joystick import ArmJoystick, CalibrationProfile
from summit_signal.model_contract import (
    ModelContractError,
    resolve_model_path,
    verify_official_model,
)
from summit_signal.rehearsal import scripted_goal
from summit_signal.safety import ArmCommand, ArmSafetyController, SafetyState


class SimulationContractError(RuntimeError):
    """Raised when the MuJoCo model does not expose the expected 29-DOF contract."""


class G1ArmSimulation:
    """Torque-level arm rehearsal with a deliberate non-physical lower-body brace."""

    def __init__(self, model_path: Path, *, kp: float = 20.0, kd: float = 1.5) -> None:
        import mujoco
        import numpy as np

        self.mujoco = mujoco
        self.np = np
        self.kp = kp
        self.kd = kd
        self.model = mujoco.MjModel.from_xml_path(str(model_path))
        self.data = mujoco.MjData(self.model)
        self._all_joint_qpos: list[int] = []
        self._all_joint_dof: list[int] = []
        for joint_name in G1_29DOF_MODEL_JOINTS:
            joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
            if joint_id < 0:
                raise SimulationContractError(f"official model is missing {joint_name}")
            self._all_joint_qpos.append(int(self.model.jnt_qposadr[joint_id]))
            self._all_joint_dof.append(int(self.model.jnt_dofadr[joint_id]))
        self._joint_qpos: list[int] = []
        self._joint_dof: list[int] = []
        self._actuators: list[int] = []
        for spec in CONTROLLED_JOINTS:
            joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, spec.model_name)
            actuator_name = spec.model_name.removesuffix("_joint")
            actuator_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_name)
            if joint_id < 0 or actuator_id < 0:
                raise SimulationContractError(f"official model is missing {spec.model_name}")
            joint_range = self.model.jnt_range[joint_id]
            if not np.allclose(joint_range, [spec.hard_min_rad, spec.hard_max_rad], atol=1e-4):
                raise SimulationContractError(f"joint limit mismatch for {spec.model_name}")
            self._joint_qpos.append(int(self.model.jnt_qposadr[joint_id]))
            self._joint_dof.append(int(self.model.jnt_dofadr[joint_id]))
            self._actuators.append(actuator_id)
        if self.model.nu != 29:
            raise SimulationContractError(f"expected 29 actuators, found {self.model.nu}")
        mujoco.mj_resetData(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)
        self._brace_qpos = self.data.qpos.copy()
        self._brace_qvel = self.data.qvel.copy()
        self.data.ctrl[:] = 0.0

    def positions(self) -> tuple[float, ...]:
        return tuple(float(self.data.qpos[index]) for index in self._all_joint_qpos)

    def velocities(self) -> tuple[float, ...]:
        return tuple(float(self.data.qvel[index]) for index in self._all_joint_dof)

    def _restore_brace(self) -> None:
        qpos_mask = self.np.ones(self.model.nq, dtype=bool)
        qvel_mask = self.np.ones(self.model.nv, dtype=bool)
        qpos_mask[self._joint_qpos] = False
        qvel_mask[self._joint_dof] = False
        self.data.qpos[qpos_mask] = self._brace_qpos[qpos_mask]
        self.data.qvel[qvel_mask] = self._brace_qvel[qvel_mask]

    def step(self, command: ArmCommand) -> bool:
        self.data.ctrl[:] = 0.0
        if command.state is SafetyState.LATCHED:
            return False
        for target, qpos_index, dof_index, actuator_id in zip(
            command.target_positions,
            self._joint_qpos,
            self._joint_dof,
            self._actuators,
            strict=True,
        ):
            q = float(self.data.qpos[qpos_index])
            dq = float(self.data.qvel[dof_index])
            torque = self.kp * (target - q) - self.kd * dq if command.enabled else -2.0 * dq
            force_range = self.model.actuator_ctrlrange[actuator_id]
            self.data.ctrl[actuator_id] = min(max(torque, force_range[0]), force_range[1])
        self.mujoco.mj_step(self.model, self.data)
        self._restore_brace()
        self.mujoco.mj_forward(self.model, self.data)
        return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--operator-config", type=Path, default=DEFAULT_OPERATOR_CONFIG)
    parser.add_argument(
        "--hardware-config",
        type=Path,
        help="rehearse the exact real-robot commissioning parameters",
    )
    parser.add_argument("--calibration", type=Path)
    parser.add_argument(
        "--scripted",
        action="store_true",
        help="run a deterministic bounded gesture without opening a joystick",
    )
    parser.add_argument("--joystick-index", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--record-goals", type=Path)
    parser.add_argument("--record-initial-state", type=Path)
    return parser


def run(args: argparse.Namespace) -> int:
    if not 1.0 <= args.seconds <= 300.0:
        raise ConfigError("--seconds must be between 1 and 300")
    if args.scripted and args.calibration is not None:
        raise ConfigError("--scripted cannot be combined with --calibration")
    if not args.scripted and args.calibration is None:
        raise ConfigError("--calibration is required unless --scripted is used")
    if (args.record_goals is None) != (args.record_initial_state is None):
        raise ConfigError("--record-goals and --record-initial-state must be supplied together")
    if args.record_goals is not None and args.record_initial_state is not None:
        if args.record_goals.resolve() == args.record_initial_state.resolve():
            raise ConfigError("goal and initial-state recordings must use different files")
        if args.record_goals.exists() or args.record_initial_state.exists():
            raise ConfigError("recording files already exist; refusing to overwrite them")
    model_path = verify_official_model(resolve_model_path(args.model))
    operator_config = OperatorConfig.load(args.operator_config)
    hardware_config = (
        HardwareConfig.load(args.hardware_config, require_activation=False)
        if args.hardware_config
        else None
    )
    joystick: ArmJoystick | None = None
    if not args.scripted:
        assert args.calibration is not None
        calibration = CalibrationProfile.load(args.calibration, operator_config.command_axes)
        joystick = ArmJoystick(
            operator_config,
            device_index=args.joystick_index,
            calibration=calibration,
        )
    plant = G1ArmSimulation(
        model_path,
        kp=hardware_config.arm_kp if hardware_config else 20.0,
        kd=hardware_config.arm_kd if hardware_config else 1.5,
    )
    now = time.monotonic()
    controller = ArmSafetyController(
        watchdog_timeout_s=0.25,
        max_target_velocity_rad_s=(
            hardware_config.max_target_velocity_rad_s if hardware_config else 0.25
        ),
        control_period_s=(
            hardware_config.control_period_s if hardware_config else float(plant.model.opt.timestep)
        ),
        started_at=now,
        max_measured_velocity_rad_s=(
            hardware_config.max_measured_velocity_rad_s if hardware_config else 0.5
        ),
        excursion_scale=hardware_config.excursion_scale if hardware_config else 1.0,
    )
    session_id = "simulation_session"
    sequence = 0
    next_goal_at = now
    next_viewer_sync_at = now
    next_status_at = now
    deadline = now + args.seconds
    warmup_s = min(0.3, args.seconds * 0.15)
    motion_end_s = max(warmup_s + 0.5, args.seconds - 0.5)
    try:
        with ExitStack() as recordings:
            goal_stream = None
            if args.record_goals is not None and args.record_initial_state is not None:
                args.record_goals.parent.mkdir(parents=True, exist_ok=True)
                args.record_initial_state.parent.mkdir(parents=True, exist_ok=True)
                goal_stream = recordings.enter_context(args.record_goals.open("xb"))
                initial_state = {
                    "schema_version": 1,
                    "positions": list(plant.positions()),
                }
                with args.record_initial_state.open("x", encoding="utf-8") as stream:
                    json.dump(initial_state, stream, indent=2)
                    stream.write("\n")
            import mujoco.viewer

            with mujoco.viewer.launch_passive(plant.model, plant.data) as viewer:
                while viewer.is_running() and time.monotonic() < deadline:
                    cycle_start = time.monotonic()
                    if cycle_start >= next_goal_at:
                        if joystick is None:
                            goal = scripted_goal(
                                sequence,
                                elapsed_s=cycle_start - now,
                                sent_monotonic_s=cycle_start,
                                warmup_s=warmup_s,
                                motion_end_s=motion_end_s,
                            )
                        else:
                            goal = joystick.sample(
                                session_id=session_id,
                                sequence=sequence,
                                now=cycle_start,
                            )
                        controller.accept_goal(goal, received_at=cycle_start)
                        if goal_stream is not None:
                            goal_stream.write(goal.encode())
                            goal_stream.flush()
                        sequence += 1
                        next_goal_at += 1.0 / operator_config.goal_hz
                    command = controller.step(
                        now=cycle_start,
                        positions=plant.positions(),
                        velocities=plant.velocities(),
                    )
                    advanced = plant.step(command)
                    if cycle_start >= next_viewer_sync_at:
                        viewer.sync()
                        next_viewer_sync_at += 1.0 / 60.0
                    if cycle_start >= next_status_at:
                        print(
                            f"\r{command.state.value:<8} "
                            f"dead-man={'ON' if command.enabled else 'OFF'} "
                            "targets=("
                            f"{', '.join(f'{value:+.3f}' for value in command.target_positions)})",
                            end="",
                            file=sys.stderr,
                            flush=True,
                        )
                        next_status_at += 0.1
                    if not advanced or command.shutdown_requested:
                        break
                    remaining = plant.model.opt.timestep - (time.monotonic() - cycle_start)
                    if remaining > 0.0 and math.isfinite(remaining):
                        time.sleep(remaining)
        completed = time.monotonic() >= deadline
        stop_reason = controller.reason or ("duration complete" if completed else "viewer closed")
        print(f"\nSIMULATION STOPPED: {stop_reason}", file=sys.stderr)
        return 0
    finally:
        if joystick is not None:
            joystick.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        return run(parser.parse_args(argv))
    except (
        ConfigError,
        ModelContractError,
        SimulationContractError,
        RuntimeError,
        ValueError,
    ) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
