from summit_signal.rehearsal import scripted_goal


def test_scripted_goal_has_disarmed_active_disarmed_phases() -> None:
    before = scripted_goal(
        0,
        elapsed_s=0.1,
        sent_monotonic_s=10.1,
        warmup_s=0.3,
        motion_end_s=2.5,
    )
    active = scripted_goal(
        1,
        elapsed_s=1.0,
        sent_monotonic_s=11.0,
        warmup_s=0.3,
        motion_end_s=2.5,
    )
    after = scripted_goal(
        2,
        elapsed_s=2.6,
        sent_monotonic_s=12.6,
        warmup_s=0.3,
        motion_end_s=2.5,
    )

    assert not before.deadman and before.axes == (0.0, 0.0, 0.0)
    assert active.deadman and all(-1.0 <= axis <= 1.0 for axis in active.axes)
    assert not after.deadman and after.axes == (0.0, 0.0, 0.0)
