#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "MAC SETUP FAILED: $*" >&2
  exit 2
}

command -v git >/dev/null || fail "git is required"
command -v uv >/dev/null || fail "uv is required; install it on the Mac first"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
model_repo="${SUMMIT_SIGNAL_MODEL_REPO:-${project_root}/../unitree_mujoco}"
model_commit="4134cb5dc7ff1ba7f484deda48b5274b58694519"
model_scene="${model_repo}/unitree_robots/g1/scene_29dof.xml"

uv sync --project "${project_root}" --python 3.10 --extra simulation --extra test

if [[ ! -e "${model_repo}" ]]; then
  git clone https://github.com/unitreerobotics/unitree_mujoco.git "${model_repo}"
  git -C "${model_repo}" checkout --detach "${model_commit}"
elif [[ ! -d "${model_repo}/.git" ]]; then
  fail "${model_repo} exists but is not a Unitree MuJoCo git checkout"
fi

actual_commit="$(git -C "${model_repo}" rev-parse HEAD)"
[[ "${actual_commit}" == "${model_commit}" ]] ||
  fail "Unitree MuJoCo must be pinned to ${model_commit}; found ${actual_commit}"
[[ -f "${model_scene}" ]] || fail "missing pinned G1 scene: ${model_scene}"

uv run --project "${project_root}" --python 3.10 python -m summit_signal.smoke \
  --model "${model_scene}" \
  --hardware-config "${project_root}/config/hardware.example.json" \
  --seconds 3

echo "MAC SETUP COMPLETE: Python 3.10 environment and pinned model smoke test passed."
echo "Next: ./scripts/mac_calibrate.sh"
