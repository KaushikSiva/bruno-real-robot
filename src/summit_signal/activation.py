"""Create a real-robot config from the checked-in, simulation-tested profile."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from summit_signal.config import ConfigError, HardwareConfig


def prepare_hardware_config(
    source: Path,
    output: Path,
    *,
    facility_rules_acknowledged: bool,
    personal_access_confirmed: bool,
    simulation_passed: bool,
) -> Path:
    confirmations = {
        "--facility-rules-acknowledged": facility_rules_acknowledged,
        "--personal-access-confirmed": personal_access_confirmed,
        "--simulation-passed": simulation_passed,
    }
    missing = [flag for flag, confirmed in confirmations.items() if not confirmed]
    if missing:
        raise ConfigError(f"refusing activation without {' '.join(missing)}")
    if output.exists():
        raise ConfigError(f"refusing to overwrite existing configuration: {output}")

    HardwareConfig.load(source, require_activation=False)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        raw["hardware_activation"]["enabled"] = True
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot prepare hardware configuration: {error}") from error

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(raw, stream, indent=2)
            stream.write("\n")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    HardwareConfig.load(output)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("config/hardware.example.json"))
    parser.add_argument("--output", type=Path, default=Path("runtime/hardware.json"))
    parser.add_argument("--facility-rules-acknowledged", action="store_true")
    parser.add_argument("--personal-access-confirmed", action="store_true")
    parser.add_argument("--simulation-passed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output = prepare_hardware_config(
            args.source,
            args.output,
            facility_rules_acknowledged=args.facility_rules_acknowledged,
            personal_access_confirmed=args.personal_access_confirmed,
            simulation_passed=args.simulation_passed,
        )
    except ConfigError as error:
        parser.error(str(error))
    print(f"Created hardware-activated commissioning config: {output}")
    print("This records operator acknowledgements; it does not claim administrator approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
