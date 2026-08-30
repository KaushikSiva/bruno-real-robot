import sys
from pathlib import Path

import pytest

from summit_signal.config import OperatorConfig
from summit_signal.joystick import (
    ArmJoystick,
    AxisCalibration,
    CalibrationProfile,
    capture_calibration,
)


class FakeDevice:
    def __init__(self) -> None:
        self.axes = [0.5, -0.5, 0.0, 0.25, -1.0, -1.0]
        self.buttons = [0] * 10
        self.initialized = False

    def init(self) -> None:
        self.initialized = True

    def quit(self) -> None:
        self.initialized = False

    def get_init(self) -> bool:
        return self.initialized

    def get_name(self) -> str:
        return "Mock DualSense"

    def get_numaxes(self) -> int:
        return len(self.axes)

    def get_axis(self, index: int) -> float:
        return self.axes[index]

    def get_numbuttons(self) -> int:
        return len(self.buttons)

    def get_button(self, index: int) -> int:
        return self.buttons[index]


class FakeJoystickModule:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device

    def init(self) -> None:
        return None

    def quit(self) -> None:
        return None

    def get_count(self) -> int:
        return 1

    def Joystick(self, index: int) -> FakeDevice:
        assert index == 0
        return self.device


class FakeController:
    def __init__(self) -> None:
        self.initialized = True
        self.is_attached = True

    def attached(self) -> bool:
        return self.is_attached

    def get_init(self) -> bool:
        return self.initialized

    def quit(self) -> None:
        self.initialized = False


class FakeControllerModule:
    def __init__(self) -> None:
        self.controller = FakeController()
        self.update_count = 0

    def init(self) -> None:
        return None

    def quit(self) -> None:
        return None

    def update(self) -> None:
        self.update_count += 1

    def is_controller(self, index: int) -> bool:
        return index == 0

    def Controller(self, index: int) -> FakeController:
        assert index == 0
        return self.controller


class FakePygame:
    error = RuntimeError

    def __init__(self) -> None:
        self.device = FakeDevice()
        self.joystick = FakeJoystickModule(self.device)


@pytest.fixture
def fake_pygame(monkeypatch: pytest.MonkeyPatch) -> tuple[FakePygame, FakeControllerModule]:
    pygame = FakePygame()
    controllers = FakeControllerModule()
    monkeypatch.setitem(sys.modules, "pygame", pygame)
    monkeypatch.setitem(sys.modules, "pygame._sdl2.controller_old", controllers)
    return pygame, controllers


def config() -> OperatorConfig:
    return OperatorConfig(
        deadzone=0.1,
        axis_shoulder_pitch=1,
        axis_shoulder_roll=0,
        axis_elbow=3,
        invert_shoulder_pitch=True,
        invert_shoulder_roll=False,
        invert_elbow=True,
        deadman_button=4,
        emergency_stop_button=1,
        emergency_stop_modifier_axis=5,
        emergency_stop_modifier_threshold=0.5,
        quit_button=9,
        goal_hz=10.0,
    )


def calibration() -> CalibrationProfile:
    return CalibrationProfile(
        "Mock DualSense",
        {index: AxisCalibration(-1.0, 0.0, 1.0) for index in (0, 1, 3)},
    )


def test_arm_mapping_and_deadman(fake_pygame) -> None:
    pygame, _ = fake_pygame
    pygame.device.buttons[4] = 1
    joystick = ArmJoystick(config(), device_index=0, calibration=calibration())

    goal = joystick.sample(session_id="session_1234", sequence=0, now=1.0)

    assert goal.axes == pytest.approx((4 / 9, 4 / 9, -(1.5 / 9)))
    assert goal.deadman
    assert not goal.emergency_stop
    joystick.close()


def test_hot_unplug_returns_terminal_stop(fake_pygame) -> None:
    _, controllers = fake_pygame
    joystick = ArmJoystick(config(), device_index=0, calibration=calibration())
    controllers.controller.is_attached = False

    goal = joystick.sample(session_id="session_1234", sequence=1, now=2.0)

    assert not goal.connected
    assert goal.emergency_stop
    assert goal.quit
    assert not goal.deadman
    assert goal.axes == (0.0, 0.0, 0.0)
    joystick.close()


def test_circle_without_r2_does_not_request_emergency_stop(fake_pygame) -> None:
    pygame, _ = fake_pygame
    pygame.device.buttons[1] = 1
    joystick = ArmJoystick(config(), device_index=0, calibration=calibration())

    goal = joystick.sample(session_id="session_1234", sequence=2, now=3.0)

    assert not goal.emergency_stop
    joystick.close()


def test_r2_and_circle_chord_and_options_are_terminal_flags(fake_pygame) -> None:
    pygame, _ = fake_pygame
    pygame.device.buttons[1] = 1
    pygame.device.axes[5] = 1.0
    pygame.device.buttons[9] = 1
    joystick = ArmJoystick(config(), device_index=0, calibration=calibration())

    goal = joystick.sample(session_id="session_1234", sequence=2, now=3.0)

    assert goal.emergency_stop
    assert goal.quit
    joystick.close()


def test_calibration_explicitly_updates_controller_without_a_video_event_loop(
    fake_pygame, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pygame, controllers = fake_pygame

    class FakeClock:
        now = 0.0

        def monotonic(self) -> float:
            self.now += 0.1
            return self.now

        def sleep(self, _seconds: float) -> None:
            return None

    clock = FakeClock()
    axis_sample = 0

    def get_axis(_index: int) -> float:
        nonlocal axis_sample
        if clock.now < 1.0:
            return 0.0
        axis_sample += 1
        return -1.0 if axis_sample % 2 else 1.0

    pygame.device.get_axis = get_axis
    monkeypatch.setattr("summit_signal.joystick.time.monotonic", clock.monotonic)
    monkeypatch.setattr("summit_signal.joystick.time.sleep", clock.sleep)
    output = tmp_path / "calibration.json"

    profile = capture_calibration(
        config(),
        output,
        device_index=0,
        sweep_seconds=2.0,
        prompt=lambda _message: "",
    )

    assert output.is_file()
    assert profile.device_name == "Mock DualSense"
    assert controllers.update_count > 0
    assert not controllers.controller.initialized
    assert not pygame.device.initialized


def test_calibration_reports_axes_that_only_move_to_one_side(
    fake_pygame, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pygame, _ = fake_pygame

    class FakeClock:
        now = 0.0

        def monotonic(self) -> float:
            self.now += 0.1
            return self.now

        def sleep(self, _seconds: float) -> None:
            return None

    clock = FakeClock()
    pygame.device.get_axis = lambda _index: 0.0 if clock.now < 1.0 else 1.0
    monkeypatch.setattr("summit_signal.joystick.time.monotonic", clock.monotonic)
    monkeypatch.setattr("summit_signal.joystick.time.sleep", clock.sleep)

    with pytest.raises(
        RuntimeError, match=r"axes did not move to both sides.*axis 0.*axis 1.*axis 3"
    ):
        capture_calibration(
            config(),
            tmp_path / "calibration.json",
            device_index=0,
            sweep_seconds=2.0,
            prompt=lambda _message: "",
        )

    assert not pygame.device.initialized
