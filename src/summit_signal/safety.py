"""Hardware-independent, restart-to-clear arm command safety state machine."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from summit_signal.config import CONTROLLED_JOINTS
from summit_signal.protocol import ArmGoal


class SafetyState(str, Enum):
    DISARMED = "DISARMED"
    ACTIVE = "ACTIVE"
    LATCHED = "LATCHED"


@dataclass(frozen=True)
class ArmCommand:
    state: SafetyState
    enabled: bool
    target_positions: tuple[float, float, float]
    reason: str | None = None
    shutdown_requested: bool = False


class ArmSafetyController:
    """Convert normalized operator goals to bounded, slew-limited joint targets."""

    def __init__(
        self,
        *,
        watchdog_timeout_s: float,
        max_target_velocity_rad_s: float,
        control_period_s: float,
        started_at: float,
        max_measured_velocity_rad_s: float | None = None,
        excursion_scale: float = 1.0,
    ) -> None:
        if not 0.0 < control_period_s <= watchdog_timeout_s:
            raise ValueError("control period must be positive and no greater than the watchdog")
        if not all(
            math.isfinite(value)
            for value in (
                watchdog_timeout_s,
                max_target_velocity_rad_s,
                control_period_s,
                started_at,
            )
        ):
            raise ValueError("controller timing and velocity values must be finite")
        if max_target_velocity_rad_s <= 0.0 or started_at < 0.0:
            raise ValueError("maximum velocity must be positive and start time non-negative")
        if max_measured_velocity_rad_s is not None and (
            not math.isfinite(max_measured_velocity_rad_s) or max_measured_velocity_rad_s <= 0.0
        ):
            raise ValueError("measured velocity limit must be finite and positive")
        if not math.isfinite(excursion_scale) or not 0.0 < excursion_scale <= 1.0:
            raise ValueError("excursion scale must be finite and in (0, 1]")
        self.watchdog_timeout_s = watchdog_timeout_s
        self.max_target_velocity_rad_s = max_target_velocity_rad_s
        self.max_measured_velocity_rad_s = max_measured_velocity_rad_s
        self.excursion_scale = excursion_scale
        self.control_period_s = control_period_s
        self.started_at = started_at
        self.state = SafetyState.DISARMED
        self.reason: str | None = None
        self.shutdown_requested = False
        self._goal: ArmGoal | None = None
        self._session_id: str | None = None
        self._last_sequence = -1
        self._last_goal_received_at: float | None = None
        self._last_step_at = started_at
        self._deadman_was_held = False
        self._neutral: tuple[float, float, float] | None = None
        self._targets: tuple[float, float, float] | None = None

    def latch(self, reason: str, *, shutdown: bool = True) -> None:
        if self.state is SafetyState.LATCHED:
            self.shutdown_requested = self.shutdown_requested or shutdown
            return
        self.state = SafetyState.LATCHED
        self.reason = reason[:160]
        self.shutdown_requested = self.shutdown_requested or shutdown
        self._deadman_was_held = False

    def accept_goal(self, goal: ArmGoal, *, received_at: float) -> bool:
        if self.state is SafetyState.LATCHED:
            return False
        if not math.isfinite(received_at) or received_at < self.started_at:
            self.latch("invalid receiver clock")
            return False
        if self._last_goal_received_at is not None and received_at < self._last_goal_received_at:
            self.latch("receiver clock moved backwards")
            return False
        if self._session_id is not None and goal.session_id != self._session_id:
            self.latch("operator session changed")
            return False
        if goal.sequence <= self._last_sequence:
            return False
        self._session_id = goal.session_id
        self._last_sequence = goal.sequence
        self._last_goal_received_at = received_at
        self._goal = goal
        if goal.emergency_stop or not goal.connected:
            self.latch("operator emergency stop" if goal.connected else "joystick disconnected")
        elif goal.quit:
            self.latch("operator requested shutdown")
        return True

    @staticmethod
    def _current_positions(positions: Sequence[float]) -> tuple[float, float, float]:
        if len(positions) < 29:
            raise ValueError("G1 state must contain at least 29 motor positions")
        values = tuple(float(positions[spec.dds_index]) for spec in CONTROLLED_JOINTS)
        for spec, value in zip(CONTROLLED_JOINTS, values, strict=True):
            if not math.isfinite(value):
                raise ValueError("controlled joint state is not finite")
            if not spec.hard_min_rad <= value <= spec.hard_max_rad:
                raise ValueError(f"controlled joint state is outside the limit for {spec.name}")
        return values

    def _latched_command(self, current: tuple[float, float, float]) -> ArmCommand:
        return ArmCommand(
            state=SafetyState.LATCHED,
            enabled=False,
            target_positions=current,
            reason=self.reason,
            shutdown_requested=self.shutdown_requested,
        )

    @staticmethod
    def _current_velocities(velocities: Sequence[float]) -> tuple[float, float, float]:
        if len(velocities) < 29:
            raise ValueError("G1 velocity state must contain at least 29 motors")
        values = tuple(float(velocities[spec.dds_index]) for spec in CONTROLLED_JOINTS)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("controlled joint velocity is not finite")
        return values

    def step(
        self,
        *,
        now: float,
        positions: Sequence[float],
        velocities: Sequence[float] | None = None,
    ) -> ArmCommand:
        current = self._current_positions(positions)
        if not math.isfinite(now) or now < self._last_step_at:
            self.latch("control clock moved backwards")
        if self.state is SafetyState.LATCHED:
            return self._latched_command(current)
        if self.max_measured_velocity_rad_s is not None:
            if velocities is None:
                self.latch("measured joint velocity unavailable")
                return self._latched_command(current)
            measured = self._current_velocities(velocities)
            for spec, velocity in zip(CONTROLLED_JOINTS, measured, strict=True):
                if abs(velocity) > self.max_measured_velocity_rad_s:
                    self.latch(f"measured velocity limit exceeded for {spec.name}")
                    return self._latched_command(current)
        if self._last_goal_received_at is None:
            self._last_step_at = now
            return ArmCommand(SafetyState.DISARMED, False, current)
        if now - self._last_goal_received_at >= self.watchdog_timeout_s:
            self.latch("operator goal watchdog expired")
            return self._latched_command(current)
        assert self._goal is not None
        if not self._goal.deadman:
            self.state = SafetyState.DISARMED
            self._deadman_was_held = False
            self._targets = current
            self._last_step_at = now
            return ArmCommand(self.state, False, current)

        if not self._deadman_was_held:
            if self._neutral is None:
                # Anchor the entire process to one measured pose. Releasing and
                # pressing the dead-man cannot ratchet the envelope outward.
                self._neutral = current
            self._targets = current
            self._deadman_was_held = True
            # A delayed network goal must not grant a large first-step slew.
            self._last_step_at = now
        assert self._neutral is not None and self._targets is not None
        desired = []
        sources = zip(CONTROLLED_JOINTS, self._neutral, self._goal.axes, strict=True)
        for spec, center, axis in sources:
            target = center + axis * spec.max_excursion_rad * self.excursion_scale
            desired.append(min(max(target, spec.hard_min_rad), spec.hard_max_rad))

        dt = min(max(now - self._last_step_at, 0.0), self.watchdog_timeout_s)
        if dt == 0.0:
            dt = self.control_period_s
        maximum_delta = self.max_target_velocity_rad_s * dt
        limited = tuple(
            previous + min(max(target - previous, -maximum_delta), maximum_delta)
            for previous, target in zip(self._targets, desired, strict=True)
        )
        self._targets = limited
        self._last_step_at = now
        self.state = SafetyState.ACTIVE
        return ArmCommand(self.state, True, limited)
