import hashlib
from pathlib import Path

import pytest

from summit_signal import model_contract


def test_model_contract_accepts_matching_scene_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "g1_29dof.xml"
    model.write_bytes(b"official-model-test")
    scene = tmp_path / "scene_29dof.xml"
    scene.write_text('<mujoco><include file="g1_29dof.xml"/></mujoco>', encoding="utf-8")
    monkeypatch.setattr(
        model_contract,
        "OFFICIAL_MODEL_SHA256",
        hashlib.sha256(model.read_bytes()).hexdigest(),
    )
    assert model_contract.verify_official_model(scene) == scene.resolve()


def test_model_contract_rejects_modified_model(tmp_path: Path) -> None:
    (tmp_path / "g1_29dof.xml").write_bytes(b"modified")
    scene = tmp_path / "scene_29dof.xml"
    scene.write_text('<mujoco><include file="g1_29dof.xml"/></mujoco>', encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        model_contract.verify_official_model(scene)
