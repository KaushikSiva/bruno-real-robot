import json

import pytest

from summit_signal.protocol import ArmGoal, ProtocolError


def goal(**overrides) -> ArmGoal:
    values = {
        "session_id": "session_1234",
        "sequence": 7,
        "sent_monotonic_s": 12.5,
        "axes": (0.1, -0.2, 0.3),
        "deadman": True,
        "emergency_stop": False,
        "quit": False,
        "connected": True,
    }
    values.update(overrides)
    return ArmGoal(**values)


def test_goal_round_trip_is_bounded_and_strict() -> None:
    encoded = goal().encode()
    assert len(encoded) <= 512
    assert encoded.endswith(b"\n")
    assert ArmGoal.decode(encoded) == goal()


def test_goal_rejects_extra_fields() -> None:
    payload = json.loads(goal().encode())
    payload["joint_targets"] = [100.0]
    with pytest.raises(ProtocolError, match="unexpected"):
        ArmGoal.decode(json.dumps(payload))


@pytest.mark.parametrize("axis", [float("nan"), float("inf"), -1.01, 1.01])
def test_goal_rejects_unbounded_or_nonfinite_axes(axis: float) -> None:
    with pytest.raises(ProtocolError, match="normalized"):
        goal(axes=(axis, 0.0, 0.0))


def test_disconnected_goal_must_stop() -> None:
    with pytest.raises(ProtocolError, match="disconnected"):
        goal(connected=False, emergency_stop=False)


def test_bool_is_not_accepted_as_numeric_sequence() -> None:
    with pytest.raises(ProtocolError, match="sequence"):
        goal(sequence=True)
