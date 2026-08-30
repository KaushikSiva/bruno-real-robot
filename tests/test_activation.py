from pathlib import Path

import pytest

from summit_signal.activation import prepare_hardware_config
from summit_signal.config import ConfigError, HardwareConfig


def example_config() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "hardware.example.json"


def test_prepare_creates_private_hardware_enabled_config(tmp_path: Path) -> None:
    output = tmp_path / "runtime" / "hardware.json"

    prepared = prepare_hardware_config(
        example_config(),
        output,
        facility_rules_acknowledged=True,
        personal_access_confirmed=True,
        simulation_passed=True,
    )

    assert prepared == output
    assert output.stat().st_mode & 0o777 == 0o600
    assert HardwareConfig.load(output).hardware_enabled


@pytest.mark.parametrize(
    "missing",
    ["facility_rules_acknowledged", "personal_access_confirmed", "simulation_passed"],
)
def test_prepare_requires_every_acknowledgement(tmp_path: Path, missing: str) -> None:
    confirmations = {
        "facility_rules_acknowledged": True,
        "personal_access_confirmed": True,
        "simulation_passed": True,
    }
    confirmations[missing] = False

    with pytest.raises(ConfigError, match=missing.replace("_", "-")):
        prepare_hardware_config(example_config(), tmp_path / "hardware.json", **confirmations)


def test_prepare_never_overwrites_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "hardware.json"
    output.write_text("user data", encoding="utf-8")

    with pytest.raises(ConfigError, match="overwrite"):
        prepare_hardware_config(
            example_config(),
            output,
            facility_rules_acknowledged=True,
            personal_access_confirmed=True,
            simulation_passed=True,
        )
    assert output.read_text(encoding="utf-8") == "user data"
