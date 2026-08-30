import argparse
import importlib
import io
import json
import sys
from pathlib import Path

import pytest

from summit_signal.config import HardwareConfig
from summit_signal.onboard import (
    STATUS_PERIOD_S,
    GoalReceiver,
    ReceivedGoal,
    _validated_initial_goal,
    build_parser,
    run,
    validate_activation,
)
from summit_signal.protocol import ArmGoal


def hardware_config() -> HardwareConfig:
    return HardwareConfig(
        hardware_enabled=True,
        facility_rules_reference="facility-rules",
        joint_map_confirmation="unitree_g1_29dof_idl_indices_0_28",
        sdk_api_confirmation="facility_unitree_sdk2py_g1_lowcmd_api_cyclonedds_0.10.2",
        command_scope_confirmation=("lowcmd_all_29_damping_right_arm_22_23_25_position_only"),
        robot_variant="g1_29dof",
        motion_profile="commissioning",
        command_topic="rt/lowcmd",
        state_topic="rt/lowstate",
        shutdown_strategy="lowcmd_all_29_joint_damping",
        control_hz=500.0,
        watchdog_timeout_s=0.25,
        initial_goal_timeout_s=10.0,
        low_state_timeout_s=0.25,
        max_target_velocity_rad_s=0.25,
        max_measured_velocity_rad_s=0.5,
        arm_kp=40.0,
        arm_kd=1.0,
        all_joint_damping_kd=8.0,
        enable_gain_ramp_s=3.0,
        shutdown_damping_duration_s=1.0,
    )


class PipeStdin:
    def __init__(self, payload: bytes = b"") -> None:
        self.buffer = io.BytesIO(payload)

    def isatty(self) -> bool:
        return False


class TtyStdin:
    def isatty(self) -> bool:
        return True


def activation_args(**overrides) -> argparse.Namespace:
    values = {
        "real_robot": True,
        "facility_rules_acknowledged": True,
        "exclusive_access_confirmed": True,
        "camera_confirmed": True,
        "developer_mode_confirmed": True,
        "motion_profile": "commissioning",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_importing_hardware_modules_does_not_import_unitree_sdk() -> None:
    before = {name for name in sys.modules if name.startswith("unitree_sdk2py")}
    importlib.import_module("summit_signal.onboard")
    importlib.import_module("summit_signal.unitree_adapter")
    after = {name for name in sys.modules if name.startswith("unitree_sdk2py")}
    assert after == before


def test_terminal_status_is_throttled_to_five_hz() -> None:
    assert STATUS_PERIOD_S == 0.2


def test_activation_requires_explicit_real_robot_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("summit_signal.onboard.sys.stdin", PipeStdin())
    args = activation_args(real_robot=False)
    with pytest.raises(ValueError, match="real-robot"):
        validate_activation(args, hardware_config())


def test_network_interface_override_is_not_exposed() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "--config",
                "hardware.json",
                "--motion-profile",
                "commissioning",
                "--network-interface",
                "eth0",
            ]
        )


def test_activation_refuses_interactive_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("summit_signal.onboard.sys.stdin", TtyStdin())
    args = activation_args()
    with pytest.raises(ValueError, match="live operator"):
        validate_activation(args, hardware_config())


@pytest.mark.parametrize(
    "flag",
    [
        "facility_rules_acknowledged",
        "exclusive_access_confirmed",
        "camera_confirmed",
        "developer_mode_confirmed",
    ],
)
def test_activation_requires_every_facility_preflight(
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    monkeypatch.setattr("summit_signal.onboard.sys.stdin", PipeStdin())
    args = activation_args(**{flag: False})

    with pytest.raises(ValueError, match=flag.replace("_", "-")):
        validate_activation(args, hardware_config())


def test_goal_receiver_rejects_oversized_lines() -> None:
    receiver = GoalReceiver(io.BytesIO(b"x" * 513 + b"\n"), clock=lambda: 1.0)
    receiver.start()
    received = receiver.take(timeout=0.2)
    assert received is not None
    assert "oversized" in str(received.error)


def test_goal_receiver_decodes_a_bounded_goal() -> None:
    goal = ArmGoal(
        session_id="session_1234",
        sequence=0,
        sent_monotonic_s=1.0,
        axes=(0.0, 0.0, 0.0),
        deadman=False,
        emergency_stop=False,
        quit=False,
        connected=True,
    )
    receiver = GoalReceiver(io.BytesIO(goal.encode()), clock=lambda: 2.0)
    receiver.start()
    received = receiver.take(timeout=0.2)
    assert received is not None
    assert received.goal == goal
    assert received.received_at == 2.0


@pytest.mark.parametrize(
    "changes",
    [
        {"deadman": True},
        {"axes": (0.1, 0.0, 0.0)},
        {"emergency_stop": True},
    ],
)
def test_initial_stream_must_be_centered_and_disarmed(changes: dict) -> None:
    values = {
        "session_id": "session_1234",
        "sequence": 0,
        "sent_monotonic_s": 1.0,
        "axes": (0.0, 0.0, 0.0),
        "deadman": False,
        "emergency_stop": False,
        "quit": False,
        "connected": True,
    }
    values.update(changes)
    received = ReceivedGoal(ArmGoal(**values), 1.0)
    with pytest.raises(ValueError, match="centered|Center|center"):
        _validated_initial_goal(received)


def hardware_config_file(path: Path) -> Path:
    raw = {
        "schema_version": 4,
        "hardware_activation": {
            "enabled": True,
            "facility_rules_reference": "facility-rules",
            "joint_map_confirmation": "unitree_g1_29dof_idl_indices_0_28",
            "sdk_api_confirmation": "facility_unitree_sdk2py_g1_lowcmd_api_cyclonedds_0.10.2",
            "command_scope_confirmation": (
                "lowcmd_all_29_damping_right_arm_22_23_25_position_only"
            ),
        },
        "robot_variant": "g1_29dof",
        "motion_profile": "commissioning",
        "topics": {"command": "rt/lowcmd", "state": "rt/lowstate"},
        "shutdown_strategy": "lowcmd_all_29_joint_damping",
        "control": {
            "control_hz": 500.0,
            "watchdog_timeout_s": 0.25,
            "initial_goal_timeout_s": 2.0,
            "low_state_timeout_s": 0.25,
            "max_target_velocity_rad_s": 0.25,
            "max_measured_velocity_rad_s": 0.5,
            "arm_kp": 40.0,
            "arm_kd": 1.0,
            "all_joint_damping_kd": 8.0,
            "enable_gain_ramp_s": 3.0,
            "shutdown_damping_duration_s": 1.0,
        },
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class FakeAdapter:
    def __init__(self, config, *, release_fails: bool = False) -> None:
        del config
        self.calls: list[str] = []
        self.release_fails = release_fails

    def connect(self) -> None:
        self.calls.append("connect")

    def wait_for_state(self, timeout: float) -> object:
        assert timeout == 2.0
        self.calls.append("wait_for_state")
        return object()

    def release(self) -> None:
        self.calls.append("release")
        if self.release_fails:
            raise RuntimeError("mock release failure")


def centered_goal() -> ArmGoal:
    return ArmGoal(
        session_id="session_1234",
        sequence=0,
        sent_monotonic_s=1.0,
        axes=(0.0, 0.0, 0.0),
        deadman=False,
        emergency_stop=False,
        quit=False,
        connected=True,
    )


def test_onboard_eof_releases_fake_adapter_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAdapter(None)
    monkeypatch.setattr("summit_signal.onboard.sys.stdin", PipeStdin(centered_goal().encode()))
    args = activation_args(
        config=hardware_config_file(tmp_path / "hardware.json"),
    )

    assert run(args, adapter_factory=lambda config: fake) == 0
    assert fake.calls == ["connect", "wait_for_state", "release"]


def test_onboard_reports_release_failure_as_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAdapter(None, release_fails=True)
    monkeypatch.setattr("summit_signal.onboard.sys.stdin", PipeStdin(centered_goal().encode()))
    args = activation_args(
        config=hardware_config_file(tmp_path / "hardware.json"),
    )

    assert run(args, adapter_factory=lambda config: fake) == 3
    assert fake.calls[-1] == "release"


def test_invalid_initial_goal_is_rejected_before_adapter_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = centered_goal()
    unsafe = ArmGoal(
        session_id=initial.session_id,
        sequence=initial.sequence,
        sent_monotonic_s=initial.sent_monotonic_s,
        axes=initial.axes,
        deadman=True,
        emergency_stop=False,
        quit=False,
        connected=True,
    )
    monkeypatch.setattr("summit_signal.onboard.sys.stdin", PipeStdin(unsafe.encode()))
    args = activation_args(
        config=hardware_config_file(tmp_path / "hardware.json"),
    )

    def forbidden_factory(config):
        del config
        raise AssertionError("adapter must not be constructed")

    with pytest.raises(ValueError, match="centered"):
        run(args, adapter_factory=forbidden_factory)
