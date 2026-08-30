from summit_signal.protocol import ArmGoal
from summit_signal.replay import replay_goals


def test_offline_replay_stays_inside_excursion_limits() -> None:
    goals = [
        ArmGoal(
            session_id="session_1234",
            sequence=sequence,
            sent_monotonic_s=sequence / 10,
            axes=(1.0, -1.0, 1.0),
            deadman=True,
            emergency_stop=False,
            quit=False,
            connected=True,
        )
        for sequence in range(30)
    ]
    result = replay_goals(goals, (0.0,) * 29)
    assert result["safe"]
    assert result["goals_accepted"] == len(goals)
    assert result["final_state"] == "ACTIVE"
