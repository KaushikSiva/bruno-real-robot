import json
from pathlib import Path

import pytest

from summit_signal.config import ConfigError, HardwareConfig, OperatorConfig


def valid_hardware() -> dict:
    return {
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
            "initial_goal_timeout_s": 10.0,
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


def write_config(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_packaged_operator_config_is_valid() -> None:
    config = OperatorConfig.load()
    assert config.deadman_button == 4
    assert config.emergency_stop_button == 1
    assert config.emergency_stop_modifier_axis == 5
    assert config.emergency_stop_modifier_threshold == 0.5
    assert config.command_axes == (1, 0, 3)


def test_checked_in_hardware_example_is_deliberately_not_runnable() -> None:
    example = Path(__file__).resolve().parents[1] / "config" / "hardware.example.json"
    with pytest.raises(ConfigError, match="activation|joint map|finite"):
        HardwareConfig.load(example)


def test_hardware_config_accepts_only_completed_activation_values(tmp_path: Path) -> None:
    config = HardwareConfig.load(write_config(tmp_path / "hardware.json", valid_hardware()))
    assert config.hardware_enabled
    assert config.control_period_s == pytest.approx(0.002)
    assert config.excursion_scale == pytest.approx(0.1)


def test_facility_commissioning_profile_is_simulation_ready_but_hardware_locked() -> None:
    profile = Path(__file__).resolve().parents[1] / "config" / "hardware.example.json"

    config = HardwareConfig.load(profile, require_activation=False)

    assert config.motion_profile == "commissioning"
    assert config.command_topic == "rt/lowcmd"
    assert config.all_joint_damping_kd == 8.0
    with pytest.raises(ConfigError, match="activation"):
        HardwareConfig.load(profile)


def test_hardware_config_rejects_inactive_file(tmp_path: Path) -> None:
    raw = valid_hardware()
    raw["hardware_activation"] = {
        "enabled": False,
        "facility_rules_reference": "",
        "joint_map_confirmation": "unitree_g1_29dof_idl_indices_0_28",
        "sdk_api_confirmation": "facility_unitree_sdk2py_g1_lowcmd_api_cyclonedds_0.10.2",
        "command_scope_confirmation": ("lowcmd_all_29_damping_right_arm_22_23_25_position_only"),
    }
    with pytest.raises(ConfigError, match="activation"):
        HardwareConfig.load(write_config(tmp_path / "hardware.json", raw))


def test_hardware_config_rejects_arm_sdk_topic(tmp_path: Path) -> None:
    raw = valid_hardware()
    raw["topics"]["command"] = "rt/arm_sdk"
    with pytest.raises(ConfigError, match="facility"):
        HardwareConfig.load(write_config(tmp_path / "hardware.json", raw))


def test_hardware_config_rejects_gain_over_official_example_bound(tmp_path: Path) -> None:
    raw = valid_hardware()
    raw["control"]["arm_kp"] = 41.0
    with pytest.raises(ConfigError, match="arm_kp"):
        HardwareConfig.load(write_config(tmp_path / "hardware.json", raw))


def test_hardware_config_rejects_excessive_measured_velocity_trip(tmp_path: Path) -> None:
    raw = valid_hardware()
    raw["control"]["max_measured_velocity_rad_s"] = 0.76
    with pytest.raises(ConfigError, match="max_measured_velocity_rad_s"):
        HardwareConfig.load(write_config(tmp_path / "hardware.json", raw))
