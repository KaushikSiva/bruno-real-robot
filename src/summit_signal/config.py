"""Strict configuration and the official 29-DOF G1 arm joint contract."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_OPERATOR_CONFIG = PACKAGE_ROOT / "config" / "operator_dualsense.json"


class ConfigError(ValueError):
    """Raised when a configuration cannot satisfy the safety contract."""


@dataclass(frozen=True)
class JointSpec:
    name: str
    model_name: str
    dds_index: int
    hard_min_rad: float
    hard_max_rad: float
    max_excursion_rad: float


# IDL indices and limits are from Unitree's official 29-DOF mapping/model.
CONTROLLED_JOINTS = (
    JointSpec(
        "right_shoulder_pitch",
        "right_shoulder_pitch_joint",
        22,
        -3.0892,
        2.6704,
        math.radians(10.0),
    ),
    JointSpec(
        "right_shoulder_roll",
        "right_shoulder_roll_joint",
        23,
        -2.2515,
        1.5882,
        math.radians(10.0),
    ),
    JointSpec(
        "right_elbow",
        "right_elbow_joint",
        25,
        -1.0472,
        2.0944,
        math.radians(15.0),
    ),
)
ALL_ARM_DDS_INDICES = tuple(range(15, 29))
G1_29DOF_MODEL_JOINTS = (
    "left_hip_pitch_joint",
    "left_hip_roll_joint",
    "left_hip_yaw_joint",
    "left_knee_joint",
    "left_ankle_pitch_joint",
    "left_ankle_roll_joint",
    "right_hip_pitch_joint",
    "right_hip_roll_joint",
    "right_hip_yaw_joint",
    "right_knee_joint",
    "right_ankle_pitch_joint",
    "right_ankle_roll_joint",
    "waist_yaw_joint",
    "waist_roll_joint",
    "waist_pitch_joint",
    "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint",
    "left_shoulder_yaw_joint",
    "left_elbow_joint",
    "left_wrist_roll_joint",
    "left_wrist_pitch_joint",
    "left_wrist_yaw_joint",
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
)
EXPECTED_ROBOT_VARIANT = "g1_29dof"
EXPECTED_JOINT_MAP_CONFIRMATION = "unitree_g1_29dof_idl_indices_0_28"
EXPECTED_SDK_API_CONFIRMATION = "facility_unitree_sdk2py_g1_lowcmd_api_cyclonedds_0.10.2"
EXPECTED_COMMAND_SCOPE_CONFIRMATION = "lowcmd_all_29_damping_right_arm_22_23_25_position_only"
SUPPORTED_SHUTDOWN_STRATEGY = "lowcmd_all_29_joint_damping"
MOTION_PROFILE_SCALES = {"commissioning": 0.1, "demo": 1.0}


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read configuration {path}: {error}") from error
    if not isinstance(value, dict):
        raise ConfigError("configuration root must be an object")
    return value


def _require_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ConfigError(f"{context} keys mismatch; missing={missing}, extra={extra}")


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{name} must be a finite number")
    return result


@dataclass(frozen=True)
class OperatorConfig:
    deadzone: float
    axis_shoulder_pitch: int
    axis_shoulder_roll: int
    axis_elbow: int
    invert_shoulder_pitch: bool
    invert_shoulder_roll: bool
    invert_elbow: bool
    deadman_button: int
    emergency_stop_button: int
    emergency_stop_modifier_axis: int
    emergency_stop_modifier_threshold: float
    quit_button: int
    goal_hz: float

    @classmethod
    def load(cls, path: Path = DEFAULT_OPERATOR_CONFIG) -> OperatorConfig:
        raw = _read_object(path)
        expected = {
            "schema_version",
            "deadzone",
            "axes",
            "invert",
            "buttons",
            "emergency_stop_chord",
            "goal_hz",
        }
        _require_keys(raw, expected, "operator configuration")
        if raw["schema_version"] != 2:
            raise ConfigError("unsupported operator configuration schema")
        for field in ("axes", "invert", "buttons", "emergency_stop_chord"):
            if not isinstance(raw[field], dict):
                raise ConfigError(f"operator {field} must be an object")
        _require_keys(raw["axes"], {"shoulder_pitch", "shoulder_roll", "elbow"}, "axes")
        _require_keys(raw["invert"], {"shoulder_pitch", "shoulder_roll", "elbow"}, "invert")
        _require_keys(raw["buttons"], {"deadman", "quit"}, "buttons")
        _require_keys(
            raw["emergency_stop_chord"],
            {"button", "modifier_axis", "modifier_threshold"},
            "emergency_stop_chord",
        )
        deadzone = _finite_number(raw["deadzone"], "deadzone")
        goal_hz = _finite_number(raw["goal_hz"], "goal_hz")
        stop_threshold = _finite_number(
            raw["emergency_stop_chord"]["modifier_threshold"],
            "emergency_stop_chord.modifier_threshold",
        )
        if not 0.05 <= deadzone <= 0.25:
            raise ConfigError("deadzone must be between 0.05 and 0.25")
        if not 5.0 <= goal_hz <= 20.0:
            raise ConfigError("goal_hz must be between 5 and 20")
        if not 0.5 <= stop_threshold <= 0.95:
            raise ConfigError("emergency stop modifier threshold must be between 0.5 and 0.95")
        indices = [
            *raw["axes"].values(),
            *raw["buttons"].values(),
            raw["emergency_stop_chord"]["button"],
            raw["emergency_stop_chord"]["modifier_axis"],
        ]
        if any(type(index) is not int or index < 0 for index in indices):
            raise ConfigError("axis and button indices must be non-negative integers")
        if any(type(value) is not bool for value in raw["invert"].values()):
            raise ConfigError("invert values must be booleans")
        return cls(
            deadzone=deadzone,
            axis_shoulder_pitch=raw["axes"]["shoulder_pitch"],
            axis_shoulder_roll=raw["axes"]["shoulder_roll"],
            axis_elbow=raw["axes"]["elbow"],
            invert_shoulder_pitch=raw["invert"]["shoulder_pitch"],
            invert_shoulder_roll=raw["invert"]["shoulder_roll"],
            invert_elbow=raw["invert"]["elbow"],
            deadman_button=raw["buttons"]["deadman"],
            emergency_stop_button=raw["emergency_stop_chord"]["button"],
            emergency_stop_modifier_axis=raw["emergency_stop_chord"]["modifier_axis"],
            emergency_stop_modifier_threshold=stop_threshold,
            quit_button=raw["buttons"]["quit"],
            goal_hz=goal_hz,
        )

    @property
    def command_axes(self) -> tuple[int, int, int]:
        return self.axis_shoulder_pitch, self.axis_shoulder_roll, self.axis_elbow


@dataclass(frozen=True)
class HardwareConfig:
    hardware_enabled: bool
    facility_rules_reference: str
    joint_map_confirmation: str
    sdk_api_confirmation: str
    command_scope_confirmation: str
    robot_variant: str
    motion_profile: str
    command_topic: str
    state_topic: str
    shutdown_strategy: str
    control_hz: float
    watchdog_timeout_s: float
    initial_goal_timeout_s: float
    low_state_timeout_s: float
    max_target_velocity_rad_s: float
    max_measured_velocity_rad_s: float
    arm_kp: float
    arm_kd: float
    all_joint_damping_kd: float
    enable_gain_ramp_s: float
    shutdown_damping_duration_s: float

    @classmethod
    def load(cls, path: Path, *, require_activation: bool = True) -> HardwareConfig:
        raw = _read_object(path)
        expected = {
            "schema_version",
            "hardware_activation",
            "robot_variant",
            "motion_profile",
            "topics",
            "shutdown_strategy",
            "control",
        }
        _require_keys(raw, expected, "hardware configuration")
        if raw["schema_version"] != 4:
            raise ConfigError("unsupported hardware configuration schema")
        activation = raw["hardware_activation"]
        topics = raw["topics"]
        control = raw["control"]
        sections = (
            ("hardware_activation", activation),
            ("topics", topics),
            ("control", control),
        )
        for name, value in sections:
            if not isinstance(value, dict):
                raise ConfigError(f"{name} must be an object")
        _require_keys(
            activation,
            {
                "enabled",
                "facility_rules_reference",
                "joint_map_confirmation",
                "sdk_api_confirmation",
                "command_scope_confirmation",
            },
            "hardware_activation",
        )
        _require_keys(topics, {"command", "state"}, "topics")
        _require_keys(
            control,
            {
                "control_hz",
                "watchdog_timeout_s",
                "initial_goal_timeout_s",
                "low_state_timeout_s",
                "max_target_velocity_rad_s",
                "max_measured_velocity_rad_s",
                "arm_kp",
                "arm_kd",
                "all_joint_damping_kd",
                "enable_gain_ramp_s",
                "shutdown_damping_duration_s",
            },
            "control",
        )
        if type(activation["enabled"]) is not bool:
            raise ConfigError("hardware_activation.enabled must be a boolean")
        reference = activation["facility_rules_reference"]
        if not isinstance(reference, str):
            raise ConfigError("facility rules reference must be a string")
        if require_activation and (not activation["enabled"] or not reference.strip()):
            raise ConfigError("hardware activation and a non-empty rules reference are required")
        if activation["joint_map_confirmation"] != EXPECTED_JOINT_MAP_CONFIRMATION:
            raise ConfigError("configuration must pin the exact official 29-DOF IDL joint map")
        if activation["sdk_api_confirmation"] != EXPECTED_SDK_API_CONFIRMATION:
            raise ConfigError("configuration must pin the Unitree/CycloneDDS API contract")
        if activation["command_scope_confirmation"] != EXPECTED_COMMAND_SCOPE_CONFIRMATION:
            raise ConfigError("configuration must pin all-joint damping and the three-joint scope")
        if raw["robot_variant"] != EXPECTED_ROBOT_VARIANT:
            raise ConfigError(f"robot_variant must be {EXPECTED_ROBOT_VARIANT}")
        motion_profile = raw["motion_profile"]
        if motion_profile not in MOTION_PROFILE_SCALES:
            raise ConfigError("motion_profile must be commissioning or demo")
        if topics != {"command": "rt/lowcmd", "state": "rt/lowstate"}:
            raise ConfigError("facility rules require rt/lowcmd and rt/lowstate")
        if raw["shutdown_strategy"] != SUPPORTED_SHUTDOWN_STRATEGY:
            raise ConfigError("facility rules require an all-29-joint damping shutdown")

        numbers = {name: _finite_number(value, name) for name, value in control.items()}
        if numbers["control_hz"] != 500.0:
            raise ConfigError("control_hz must be exactly 500 Hz for the low-level controller")
        if not 0.1 <= numbers["watchdog_timeout_s"] <= 0.25:
            raise ConfigError("watchdog_timeout_s must be between 0.1 and 0.25")
        if not 2.0 <= numbers["initial_goal_timeout_s"] <= 15.0:
            raise ConfigError("initial_goal_timeout_s must be between 2 and 15")
        if not 0.1 <= numbers["low_state_timeout_s"] <= 0.25:
            raise ConfigError("low_state_timeout_s must be between 0.1 and 0.25")
        if not 0.05 <= numbers["max_target_velocity_rad_s"] <= 0.25:
            raise ConfigError("max_target_velocity_rad_s must be between 0.05 and 0.25")
        if not 0.25 <= numbers["max_measured_velocity_rad_s"] <= 0.75:
            raise ConfigError("max_measured_velocity_rad_s must be between 0.25 and 0.75")
        if not 0.0 < numbers["arm_kp"] <= 40.0:
            raise ConfigError("arm_kp must be in (0, 40]")
        if not 0.0 < numbers["arm_kd"] <= 8.0:
            raise ConfigError("arm_kd must be in (0, 8]")
        if numbers["all_joint_damping_kd"] != 8.0:
            raise ConfigError("facility rules require all_joint_damping_kd to be exactly 8")
        if not 1.0 <= numbers["enable_gain_ramp_s"] <= 5.0:
            raise ConfigError("enable_gain_ramp_s must be between 1 and 5")
        if not 0.5 <= numbers["shutdown_damping_duration_s"] <= 2.0:
            raise ConfigError("shutdown_damping_duration_s must be between 0.5 and 2")
        return cls(
            hardware_enabled=activation["enabled"],
            facility_rules_reference=reference.strip(),
            joint_map_confirmation=activation["joint_map_confirmation"],
            sdk_api_confirmation=activation["sdk_api_confirmation"],
            command_scope_confirmation=activation["command_scope_confirmation"],
            robot_variant=raw["robot_variant"],
            motion_profile=motion_profile,
            command_topic=topics["command"],
            state_topic=topics["state"],
            shutdown_strategy=raw["shutdown_strategy"],
            **numbers,
        )

    @property
    def control_period_s(self) -> float:
        return 1.0 / self.control_hz

    @property
    def excursion_scale(self) -> float:
        return MOTION_PROFILE_SCALES[self.motion_profile]
