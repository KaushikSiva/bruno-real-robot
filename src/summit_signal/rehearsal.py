"""Deterministic bounded goals shared by headless and visual simulation."""

from __future__ import annotations

import math

from summit_signal.protocol import ArmGoal


def scripted_goal(
    sequence: int,
    *,
    elapsed_s: float,
    sent_monotonic_s: float,
    warmup_s: float,
    motion_end_s: float,
) -> ArmGoal:
    """Return a disarmed/active/disarmed rehearsal goal with bounded axes."""

    active = warmup_s <= elapsed_s < motion_end_s
    if active:
        phase = (elapsed_s - warmup_s) / (motion_end_s - warmup_s)
        wave = math.sin(2.0 * math.pi * phase)
        axes = (0.8 * wave, -0.5 * wave, 0.7 * wave)
    else:
        axes = (0.0, 0.0, 0.0)
    return ArmGoal(
        session_id="scripted_simulation",
        sequence=sequence,
        sent_monotonic_s=sent_monotonic_s,
        axes=axes,
        deadman=active,
        emergency_stop=False,
        quit=False,
        connected=True,
    )
