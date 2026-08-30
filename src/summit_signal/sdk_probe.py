"""Verify the facility Python environment without initializing DDS."""

from __future__ import annotations

import importlib
import sys
from importlib.metadata import PackageNotFoundError, version


class SDKProbeError(RuntimeError):
    """Raised when the Jetson environment does not match the controller API."""


def verify_sdk_environment() -> None:
    if sys.version_info[:2] != (3, 10):
        found = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise SDKProbeError(f"Python 3.10 is required; found {found}")
    try:
        importlib.import_module("cyclonedds")
        cyclone_version = version("cyclonedds")
    except (ImportError, PackageNotFoundError) as error:
        raise SDKProbeError("cyclonedds is unavailable") from error
    if cyclone_version != "0.10.2":
        raise SDKProbeError(f"cyclonedds must be 0.10.2; found {cyclone_version}")

    required_symbols = {
        "unitree_sdk2py.core.channel": (
            "ChannelFactoryInitialize",
            "ChannelPublisher",
            "ChannelSubscriber",
        ),
        "unitree_sdk2py.idl.default": ("unitree_hg_msg_dds__LowCmd_",),
        "unitree_sdk2py.idl.unitree_hg.msg.dds_": ("LowCmd_", "LowState_"),
        "unitree_sdk2py.utils.crc": ("CRC",),
    }
    loaded = {}
    try:
        for module_name, names in required_symbols.items():
            module = importlib.import_module(module_name)
            for name in names:
                loaded[name] = getattr(module, name)
    except (ImportError, AttributeError) as error:
        raise SDKProbeError(f"facility Unitree SDK API is incompatible: {error}") from error

    message = loaded["unitree_hg_msg_dds__LowCmd_"]()
    if not hasattr(message, "motor_cmd") or len(message.motor_cmd) < 29:
        raise SDKProbeError("facility LowCmd default does not expose at least 29 motor commands")


def main() -> int:
    try:
        verify_sdk_environment()
    except SDKProbeError as error:
        print(f"SDK ENVIRONMENT FAILED: {error}", file=sys.stderr)
        return 2
    print("SDK ENVIRONMENT VERIFIED: Python 3.10, cyclonedds 0.10.2, G1 lowcmd API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
