from types import SimpleNamespace

import pytest

from summit_signal import sdk_probe


def compatible_module(name: str) -> object:
    modules = {
        "cyclonedds": SimpleNamespace(),
        "unitree_sdk2py.core.channel": SimpleNamespace(
            ChannelFactoryInitialize=object(),
            ChannelPublisher=object(),
            ChannelSubscriber=object(),
        ),
        "unitree_sdk2py.idl.default": SimpleNamespace(
            unitree_hg_msg_dds__LowCmd_=lambda: SimpleNamespace(motor_cmd=[object()] * 29)
        ),
        "unitree_sdk2py.idl.unitree_hg.msg.dds_": SimpleNamespace(
            LowCmd_=object(),
            LowState_=object(),
        ),
        "unitree_sdk2py.utils.crc": SimpleNamespace(CRC=object()),
    }
    return modules[name]


def test_sdk_probe_accepts_exact_required_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sdk_probe.sys, "version_info", (3, 10))
    monkeypatch.setattr(sdk_probe, "version", lambda _: "0.10.2")
    monkeypatch.setattr(sdk_probe.importlib, "import_module", compatible_module)

    sdk_probe.verify_sdk_environment()


def test_sdk_probe_rejects_wrong_cyclonedds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sdk_probe.sys, "version_info", (3, 10))
    monkeypatch.setattr(sdk_probe, "version", lambda _: "0.11.0")
    monkeypatch.setattr(sdk_probe.importlib, "import_module", compatible_module)

    with pytest.raises(sdk_probe.SDKProbeError, match="0.10.2"):
        sdk_probe.verify_sdk_environment()


def test_sdk_probe_rejects_short_lowcmd(monkeypatch: pytest.MonkeyPatch) -> None:
    def short_message_module(name: str) -> object:
        if name == "unitree_sdk2py.idl.default":
            return SimpleNamespace(
                unitree_hg_msg_dds__LowCmd_=lambda: SimpleNamespace(motor_cmd=[object()] * 28)
            )
        return compatible_module(name)

    monkeypatch.setattr(sdk_probe.sys, "version_info", (3, 10))
    monkeypatch.setattr(sdk_probe, "version", lambda _: "0.10.2")
    monkeypatch.setattr(sdk_probe.importlib, "import_module", short_message_module)

    with pytest.raises(sdk_probe.SDKProbeError, match="29"):
        sdk_probe.verify_sdk_environment()
