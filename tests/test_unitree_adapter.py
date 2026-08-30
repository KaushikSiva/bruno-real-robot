from dataclasses import replace

import pytest

from summit_signal.config import HardwareConfig
from summit_signal.safety import ArmCommand, SafetyState
from summit_signal.unitree_adapter import (
    G1_MOTOR_COUNT,
    RobotState,
    UnitreeAdapterError,
    UnitreeLowLevelAdapter,
)


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
        low_state_timeout_s=0.1,
        max_target_velocity_rad_s=0.1,
        max_measured_velocity_rad_s=0.5,
        arm_kp=40.0,
        arm_kd=1.0,
        all_joint_damping_kd=8.0,
        enable_gain_ramp_s=3.0,
        shutdown_damping_duration_s=1.0,
    )


class FakeMotor:
    def __init__(self) -> None:
        self.mode = 999
        self.tau = 999.0
        self.q = 999.0
        self.dq = 999.0
        self.kp = 999.0
        self.kd = 999.0


class FakeMessage:
    def __init__(self) -> None:
        self.mode_machine = 0
        self.mode_pr = 0
        self.motor_cmd = [FakeMotor() for _ in range(35)]
        self.crc = 0


class FakeCRC:
    def Crc(self, message: FakeMessage) -> int:
        del message
        return 123


class FakePublisher:
    def __init__(self) -> None:
        self.writes = 0

    def Write(self, message: FakeMessage) -> None:
        assert message.crc == 123
        self.writes += 1


def ready_adapter(config: HardwareConfig) -> tuple[UnitreeLowLevelAdapter, FakePublisher]:
    adapter = UnitreeLowLevelAdapter(config, clock=lambda: 1.0)
    publisher = FakePublisher()
    adapter._connected = True
    adapter._publisher = publisher
    adapter._message = FakeMessage()
    adapter._crc = FakeCRC()
    adapter._state = RobotState((0.0,) * 29, (0.0,) * 29, 7, 0, 1.0)
    return adapter, publisher


def test_constructor_is_inert_until_explicit_connect() -> None:
    adapter = UnitreeLowLevelAdapter(hardware_config())
    assert adapter._publisher is None
    assert not adapter._connected


def test_active_publish_damps_all_joints_and_positions_only_three() -> None:
    adapter, publisher = ready_adapter(hardware_config())
    command = ArmCommand(SafetyState.ACTIVE, True, (0.1, -0.1, 0.2))

    gain_scale = adapter.publish(command, now=1.0)

    assert gain_scale == pytest.approx((1 / 500) / 3)
    assert publisher.writes == 1
    message = adapter._message
    assert message.mode_machine == 7
    assert message.mode_pr == 0
    assert message.crc == 123
    for index in range(G1_MOTOR_COUNT):
        motor = message.motor_cmd[index]
        assert motor.mode == 1
        assert motor.tau == 0.0
        assert motor.dq == 0.0
        if index in {22, 23, 25}:
            assert motor.kp == pytest.approx(40.0 * gain_scale)
            assert motor.kd == pytest.approx(8.0 + gain_scale * (1.0 - 8.0))
        else:
            assert motor.kp == 0.0
            assert motor.kd == 8.0
    assert message.motor_cmd[22].q == pytest.approx(0.1)
    assert message.motor_cmd[23].q == pytest.approx(-0.1)
    assert message.motor_cmd[25].q == pytest.approx(0.2)
    for index in range(G1_MOTOR_COUNT, 35):
        assert message.motor_cmd[index].mode == 999


def test_disabled_publish_is_all_joint_damping() -> None:
    adapter, _ = ready_adapter(hardware_config())
    command = ArmCommand(SafetyState.DISARMED, False, (0.0, 0.0, 0.0))

    assert adapter.publish(command, now=1.0) == 0.0

    for motor in adapter._message.motor_cmd[:G1_MOTOR_COUNT]:
        assert motor.mode == 1
        assert motor.tau == 0.0
        assert motor.dq == 0.0
        assert motor.kp == 0.0
        assert motor.kd == 8.0


def test_release_leaves_damping_as_the_last_latched_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(hardware_config(), shutdown_damping_duration_s=0.5)
    adapter, publisher = ready_adapter(config)
    monkeypatch.setattr("summit_signal.unitree_adapter.time.sleep", lambda _: None)

    adapter.release()

    assert publisher.writes == 250
    for motor in adapter._message.motor_cmd[:G1_MOTOR_COUNT]:
        assert motor.kp == 0.0
        assert motor.kd == 8.0
        assert motor.tau == 0.0


def test_release_still_damps_if_dds_connected_before_first_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(hardware_config(), shutdown_damping_duration_s=0.5)
    adapter, publisher = ready_adapter(config)
    adapter._state = None
    monkeypatch.setattr("summit_signal.unitree_adapter.time.sleep", lambda _: None)

    adapter.release()

    assert publisher.writes == 250
    for motor in adapter._message.motor_cmd[:G1_MOTOR_COUNT]:
        assert motor.q == 0.0
        assert motor.kp == 0.0
        assert motor.kd == 8.0
        assert motor.tau == 0.0


def test_adapter_rejects_nonfinite_target_even_if_called_outside_controller() -> None:
    adapter, _ = ready_adapter(hardware_config())
    command = ArmCommand(SafetyState.ACTIVE, True, (float("nan"), 0.0, 0.0))
    with pytest.raises(UnitreeAdapterError, match="unsafe target"):
        adapter.publish(command, now=1.0)
