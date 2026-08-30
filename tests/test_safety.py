import math

import pytest

from summit_signal.config import CONTROLLED_JOINTS
from summit_signal.protocol import ArmGoal
from summit_signal.safety import ArmSafetyController, SafetyState


def positions(pitch: float = 0.2, roll: float = -0.1, elbow: float = 0.8) -> list[float]:
    values = [0.0] * 29
    for spec, value in zip(CONTROLLED_JOINTS, (pitch, roll, elbow), strict=False):
        values[spec.dds_index] = value
    return values


def goal(
    sequence: int,
    *,
    axes: tuple[float, float, float] = (1.0, -1.0, 1.0),
    deadman: bool = True,
    emergency_stop: bool = False,
    connected: bool = True,
    session_id: str = "session_1234",
) -> ArmGoal:
    return ArmGoal(
        session_id=session_id,
        sequence=sequence,
        sent_monotonic_s=sequence / 10,
        axes=axes,
        deadman=deadman,
        emergency_stop=emergency_stop,
        quit=False,
        connected=connected,
    )


def controller() -> ArmSafetyController:
    return ArmSafetyController(
        watchdog_timeout_s=0.25,
        max_target_velocity_rad_s=0.25,
        control_period_s=0.02,
        started_at=10.0,
    )


def test_deadman_rising_edge_centers_on_measured_pose_and_rate_limits() -> None:
    safety = controller()
    safety.accept_goal(goal(0), received_at=10.0)
    command = safety.step(now=10.02, positions=positions())

    assert command.state is SafetyState.ACTIVE
    assert command.enabled
    assert command.target_positions == pytest.approx((0.205, -0.105, 0.805))


def test_deadman_release_disarms_without_ratcheting_session_neutral() -> None:
    safety = controller()
    safety.accept_goal(goal(0), received_at=10.0)
    safety.step(now=10.02, positions=positions())
    safety.accept_goal(goal(1, deadman=False), received_at=10.1)
    released = safety.step(now=10.1, positions=positions(0.4, 0.2, 1.0))
    assert released.state is SafetyState.DISARMED
    assert released.target_positions == (0.4, 0.2, 1.0)

    safety.accept_goal(goal(2), received_at=10.2)
    rearmed = safety.step(now=10.22, positions=positions(0.4, 0.2, 1.0))
    assert rearmed.target_positions == pytest.approx((0.395, 0.195, 1.005))


def test_watchdog_latches_and_cannot_be_cleared_by_new_goal() -> None:
    safety = controller()
    safety.accept_goal(goal(0), received_at=10.0)
    stopped = safety.step(now=10.25, positions=positions())
    assert stopped.state is SafetyState.LATCHED
    assert stopped.shutdown_requested
    assert "watchdog" in str(stopped.reason)
    assert not safety.accept_goal(goal(1), received_at=10.26)


def test_latch_preserves_the_first_causal_reason() -> None:
    safety = controller()
    safety.latch("operator emergency stop")

    safety.latch("operator stream closed")

    assert safety.reason == "operator emergency stop"
    assert safety.shutdown_requested


def test_session_change_latches() -> None:
    safety = controller()
    assert safety.accept_goal(goal(0), received_at=10.0)
    assert not safety.accept_goal(goal(1, session_id="different_session"), received_at=10.1)
    assert safety.state is SafetyState.LATCHED


def test_old_or_duplicate_sequence_is_ignored() -> None:
    safety = controller()
    assert safety.accept_goal(goal(3), received_at=10.0)
    assert not safety.accept_goal(goal(3), received_at=10.1)
    assert safety.state is SafetyState.DISARMED


@pytest.mark.parametrize(
    ("emergency_stop", "connected", "reason"),
    [(True, True, "emergency"), (True, False, "disconnected")],
)
def test_stop_conditions_latch(emergency_stop: bool, connected: bool, reason: str) -> None:
    safety = controller()
    safety.accept_goal(
        goal(0, emergency_stop=emergency_stop, connected=connected),
        received_at=10.0,
    )
    command = safety.step(now=10.01, positions=positions())
    assert command.state is SafetyState.LATCHED
    assert reason in str(command.reason)


def test_nonfinite_robot_state_is_rejected() -> None:
    safety = controller()
    state = positions()
    state[CONTROLLED_JOINTS[0].dds_index] = math.nan
    with pytest.raises(ValueError, match="finite"):
        safety.step(now=10.0, positions=state)


def test_out_of_range_robot_state_is_rejected() -> None:
    safety = controller()
    state = positions()
    state[CONTROLLED_JOINTS[2].dds_index] = CONTROLLED_JOINTS[2].hard_max_rad + 0.01
    with pytest.raises(ValueError, match="outside"):
        safety.step(now=10.0, positions=state)


def test_receiver_clock_regression_latches() -> None:
    safety = controller()
    assert safety.accept_goal(goal(0), received_at=10.1)
    assert not safety.accept_goal(goal(1), received_at=10.09)
    assert safety.state is SafetyState.LATCHED


def test_measured_velocity_limit_latches_and_requires_restart() -> None:
    safety = ArmSafetyController(
        watchdog_timeout_s=0.25,
        max_target_velocity_rad_s=0.25,
        control_period_s=0.02,
        started_at=10.0,
        max_measured_velocity_rad_s=0.5,
    )
    safety.accept_goal(goal(0), received_at=10.0)
    velocities = positions(0.0, 0.0, 0.0)
    velocities[CONTROLLED_JOINTS[1].dds_index] = -0.51

    command = safety.step(now=10.02, positions=positions(), velocities=velocities)

    assert command.state is SafetyState.LATCHED
    assert command.shutdown_requested
    assert "right_shoulder_roll" in str(command.reason)


def test_measured_velocity_monitor_requires_velocity_state() -> None:
    safety = ArmSafetyController(
        watchdog_timeout_s=0.25,
        max_target_velocity_rad_s=0.25,
        control_period_s=0.02,
        started_at=10.0,
        max_measured_velocity_rad_s=0.5,
    )
    safety.accept_goal(goal(0), received_at=10.0)

    command = safety.step(now=10.02, positions=positions())

    assert command.state is SafetyState.LATCHED
    assert "unavailable" in str(command.reason)
