"""Verify the external official Unitree 29-DOF MuJoCo model before loading it."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

OFFICIAL_MODEL_SHA256 = "423e28bd718b19f7a65cda539b6f794ddbb268b4b9bdbd85f4bd982b30729617"
OFFICIAL_MODEL_FILENAME = "g1_29dof.xml"
OFFICIAL_SCENE_FILENAME = "scene_29dof.xml"


class ModelContractError(ValueError):
    """Raised when the selected MuJoCo scene is not the pinned official model."""


def resolve_model_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    configured = os.environ.get("SUMMIT_SIGNAL_MODEL")
    if configured:
        return Path(configured).expanduser().resolve()
    candidate = (
        Path(__file__).resolve().parents[4]
        / "unitree_mujoco"
        / "unitree_robots"
        / "g1"
        / OFFICIAL_SCENE_FILENAME
    )
    if candidate.is_file():
        return candidate
    raise ModelContractError(
        "provide --model or SUMMIT_SIGNAL_MODEL pointing to the pinned official scene_29dof.xml"
    )


def verify_official_model(scene_path: Path) -> Path:
    scene = scene_path.expanduser().resolve()
    if not scene.is_file() or scene.name != OFFICIAL_SCENE_FILENAME:
        raise ModelContractError("model path must be Unitree's scene_29dof.xml")
    model = scene.with_name(OFFICIAL_MODEL_FILENAME)
    if not model.is_file():
        raise ModelContractError(f"missing included model beside scene: {model}")
    try:
        scene_text = scene.read_text(encoding="utf-8")
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
    except OSError as error:
        raise ModelContractError(f"cannot verify official model: {error}") from error
    if '<include file="g1_29dof.xml"' not in scene_text:
        raise ModelContractError("scene does not include g1_29dof.xml")
    if digest != OFFICIAL_MODEL_SHA256:
        raise ModelContractError(
            "g1_29dof.xml digest mismatch; checkout the commit pinned in config/model_manifest.json"
        )
    return scene
