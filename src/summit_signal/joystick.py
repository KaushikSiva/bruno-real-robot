"""DualSense input for arm goals; this module has no robot or network access."""

from __future__ import annotations

import importlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from summit_signal.config import OperatorConfig
from summit_signal.protocol import ArmGoal

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


def apply_deadzone(value: float, deadzone: float) -> float:
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    return math.copysign((magnitude - deadzone) / (1.0 - deadzone), value)


@dataclass(frozen=True)
class AxisCalibration:
    minimum: float
    center: float
    maximum: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.minimum, self.center, self.maximum)):
            raise ValueError("axis calibration must be finite")
        if not self.minimum < self.center < self.maximum:
            raise ValueError("axis calibration requires minimum < center < maximum")

    def normalize(self, value: float, deadzone: float) -> float:
        denominator = (
            self.maximum - self.center if value >= self.center else self.center - self.minimum
        )
        centered = min(max((value - self.center) / denominator, -1.0), 1.0)
        return apply_deadzone(centered, deadzone)


@dataclass(frozen=True)
class CalibrationProfile:
    device_name: str
    axes: dict[int, AxisCalibration]

    @classmethod
    def load(cls, path: Path, required_axes: tuple[int, ...]) -> CalibrationProfile:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != 1:
                raise ValueError("unsupported calibration schema")
            profile = cls(
                device_name=str(raw["device_name"]),
                axes={
                    int(index): AxisCalibration(
                        float(values["minimum"]),
                        float(values["center"]),
                        float(values["maximum"]),
                    )
                    for index, values in raw["axes"].items()
                },
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid joystick calibration file {path}: {error}") from error
        missing = sorted(set(required_axes) - set(profile.axes))
        if missing:
            raise ValueError(f"calibration profile is missing required axes {missing}")
        return profile

    def save(self, path: Path) -> None:
        payload = {
            "schema_version": 1,
            "device_name": self.device_name,
            "axes": {
                str(index): {
                    "minimum": calibration.minimum,
                    "center": calibration.center,
                    "maximum": calibration.maximum,
                }
                for index, calibration in sorted(self.axes.items())
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ArmJoystick:
    """Read bounded arm axes and fail closed on a controller error or removal."""

    def __init__(
        self,
        config: OperatorConfig,
        *,
        device_index: int,
        calibration: CalibrationProfile,
    ) -> None:
        import pygame

        self._pygame = pygame
        self._controller_api = importlib.import_module("pygame._sdl2.controller_old")
        self._config = config
        self._calibration = calibration
        self._device = None
        self._controller = None
        self._closed = False
        pygame.joystick.init()
        self._controller_api.init()
        count = pygame.joystick.get_count()
        if count == 0:
            self.close()
            raise RuntimeError("no joystick found")
        if device_index < 0 or device_index >= count:
            self.close()
            raise RuntimeError(f"joystick index {device_index} not found; detected {count}")
        if not self._controller_api.is_controller(device_index):
            self.close()
            raise RuntimeError("selected SDL device is not a supported game controller")
        try:
            self._device = pygame.joystick.Joystick(device_index)
            self._device.init()
            self._controller = self._controller_api.Controller(device_index)
        except pygame.error:
            self.close()
            raise
        if self.name != calibration.device_name:
            self.close()
            raise RuntimeError(
                "joystick calibration device mismatch: "
                f"profile={calibration.device_name!r}, connected={self.name!r}"
            )

    @property
    def name(self) -> str:
        return self._device.get_name() if self._device is not None else "disconnected joystick"

    def _axis(self, index: int, invert: bool) -> float:
        assert self._device is not None
        if index >= self._device.get_numaxes():
            raise RuntimeError(f"joystick does not expose configured axis {index}")
        raw = float(self._device.get_axis(index))
        normalized = self._calibration.axes[index].normalize(raw, self._config.deadzone)
        return -normalized if invert else normalized

    def _button(self, index: int) -> bool:
        assert self._device is not None
        if index >= self._device.get_numbuttons():
            raise RuntimeError(f"joystick does not expose configured button {index}")
        return bool(self._device.get_button(index))

    def _emergency_stop_chord(self) -> bool:
        """Require R2 and Circle together so an accidental Circle press cannot stop."""
        assert self._device is not None
        index = self._config.emergency_stop_modifier_axis
        if index >= self._device.get_numaxes():
            raise RuntimeError(f"joystick does not expose configured axis {index}")
        modifier = float(self._device.get_axis(index))
        if not math.isfinite(modifier):
            raise RuntimeError("joystick emergency stop modifier axis is non-finite")
        return self._button(self._config.emergency_stop_button) and (
            modifier >= self._config.emergency_stop_modifier_threshold
        )

    def sample(self, *, session_id: str, sequence: int, now: float) -> ArmGoal:
        try:
            if self._device is None or self._controller is None or not self._controller.attached():
                raise RuntimeError("joystick disconnected")
            self._controller_api.update()
            axes = (
                self._axis(self._config.axis_shoulder_pitch, self._config.invert_shoulder_pitch),
                self._axis(self._config.axis_shoulder_roll, self._config.invert_shoulder_roll),
                self._axis(self._config.axis_elbow, self._config.invert_elbow),
            )
            return ArmGoal(
                session_id=session_id,
                sequence=sequence,
                sent_monotonic_s=now,
                axes=axes,
                deadman=self._button(self._config.deadman_button),
                emergency_stop=self._emergency_stop_chord(),
                quit=self._button(self._config.quit_button),
                connected=True,
            )
        except (RuntimeError, self._pygame.error):
            return ArmGoal(
                session_id=session_id,
                sequence=sequence,
                sent_monotonic_s=now,
                axes=(0.0, 0.0, 0.0),
                deadman=False,
                emergency_stop=True,
                quit=True,
                connected=False,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        controller, self._controller = self._controller, None
        device, self._device = self._device, None
        if controller is not None:
            try:
                if controller.get_init():
                    controller.quit()
            except self._pygame.error:
                pass
        if device is not None:
            try:
                if device.get_init():
                    device.quit()
            except self._pygame.error:
                pass
        self._controller_api.quit()
        self._pygame.joystick.quit()


def list_joysticks() -> list[str]:
    import pygame

    pygame.joystick.init()
    try:
        names = []
        for index in range(pygame.joystick.get_count()):
            device = pygame.joystick.Joystick(index)
            device.init()
            try:
                names.append(device.get_name())
            finally:
                device.quit()
        return names
    finally:
        pygame.joystick.quit()


def capture_calibration(
    config: OperatorConfig,
    output_path: Path,
    *,
    device_index: int,
    sweep_seconds: float,
    prompt: Callable[[str], str] = input,
) -> CalibrationProfile:
    """Record center and range for all four-stick axes used by arm control."""

    if not 2.0 <= sweep_seconds <= 30.0:
        raise ValueError("calibration sweep must be between 2 and 30 seconds")
    import pygame

    controller_api = importlib.import_module("pygame._sdl2.controller_old")
    pygame.joystick.init()
    controller_api.init()
    device = None
    controller = None
    try:
        if device_index < 0 or device_index >= pygame.joystick.get_count():
            raise RuntimeError("configured joystick index is unavailable")
        if not controller_api.is_controller(device_index):
            raise RuntimeError("selected SDL device is not a supported game controller")
        device = pygame.joystick.Joystick(device_index)
        device.init()
        controller = controller_api.Controller(device_index)
        axes = sorted(set(config.command_axes))
        if any(axis >= device.get_numaxes() for axis in axes):
            raise RuntimeError("controller does not expose every configured arm axis")
        prompt("Release both sticks to center, then press Enter.")
        samples: dict[int, list[float]] = {axis: [] for axis in axes}
        deadline = time.monotonic() + 0.75
        while time.monotonic() < deadline:
            controller_api.update()
            for axis in axes:
                samples[axis].append(float(device.get_axis(axis)))
            time.sleep(0.01)
        centers = {axis: sorted(values)[len(values) // 2] for axis, values in samples.items()}
        prompt(
            "Press Enter, then continuously rotate the LEFT stick through a full circle "
            "and move the RIGHT stick fully up and down for "
            f"{sweep_seconds:g} seconds."
        )
        minima = centers.copy()
        maxima = centers.copy()
        deadline = time.monotonic() + sweep_seconds
        while time.monotonic() < deadline:
            controller_api.update()
            for axis in axes:
                raw = float(device.get_axis(axis))
                minima[axis] = min(minima[axis], raw)
                maxima[axis] = max(maxima[axis], raw)
            time.sleep(0.01)
        one_sided_axes = [
            axis for axis in axes if not minima[axis] < centers[axis] < maxima[axis]
        ]
        if one_sided_axes:
            details = "; ".join(
                f"axis {axis}: min={minima[axis]:.3f}, "
                f"center={centers[axis]:.3f}, max={maxima[axis]:.3f}"
                for axis in one_sided_axes
            )
            raise RuntimeError(
                "these joystick axes did not move to both sides of center: "
                f"{details}; repeat calibration and follow the full-range prompt"
            )
        calibrations = {
            axis: AxisCalibration(minima[axis], centers[axis], maxima[axis]) for axis in axes
        }
        short_range_axes = [
            axis
            for axis, value in calibrations.items()
            if min(value.center - value.minimum, value.maximum - value.center) < 0.25
        ]
        if short_range_axes:
            raise RuntimeError(
                f"joystick axes {short_range_axes} did not reach enough range; "
                "repeat calibration and follow the full-range prompt"
            )
        profile = CalibrationProfile(device.get_name(), calibrations)
        profile.save(output_path)
        return profile
    finally:
        if controller is not None:
            try:
                if controller.get_init():
                    controller.quit()
            except pygame.error:
                pass
        if device is not None:
            try:
                if device.get_init():
                    device.quit()
            except pygame.error:
                pass
        controller_api.quit()
        pygame.joystick.quit()
