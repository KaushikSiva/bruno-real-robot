#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "JETSON SETUP FAILED: $*" >&2
  exit 2
}

environment_name="${1:-kaushik}"
[[ "${environment_name}" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] ||
  fail "usage: $0 [ENVIRONMENT_NAME] (default: kaushik)"
[[ "${environment_name}" != "base" ]] || fail "the shared base environment is forbidden"
command -v conda >/dev/null || fail "conda is unavailable"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
sdk_root="${UNITREE_SDK2_PYTHON_ROOT:-/opt/unitree_sdk2_python}"
wheel_cache="${HOME}/.cache/pip/wheels"

[[ -d "${sdk_root}/unitree_sdk2py" ]] ||
  fail "Unitree SDK not found at ${sdk_root}; contact the admin"
[[ -d "${wheel_cache}" ]] ||
  fail "facility pip wheel cache not found at ${wheel_cache}; contact the admin"
wheel_path="$(find "${wheel_cache}" -type f \
  -name 'cyclonedds-0.10.2-cp310-cp310-linux_aarch64.whl' -print -quit)"
[[ -n "${wheel_path}" ]] ||
  fail "cached CycloneDDS 0.10.2 Python 3.10 aarch64 wheel is unavailable; contact the admin"

if conda env list | awk -v target="${environment_name}" '$1 == target { found=1 } END { exit(found ? 0 : 1) }'; then
  fail "conda environment ${environment_name} already exists; choose your own new name"
fi

setup_complete=false
remove_partial_environment() {
  if [[ "${setup_complete}" != "true" ]]; then
    echo "Setup failed; removing partial environment ${environment_name}." >&2
    conda env remove -n "${environment_name}" -y >/dev/null 2>&1 || true
  fi
}
trap remove_partial_environment EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

conda create -n "${environment_name}" python=3.10 -y
# Persist these only inside the disposable environment. `conda run` from the
# non-interactive Mac SSH session then receives both the repository and the
# facility SDK paths without changing system or user Python.
conda env config vars set -n "${environment_name}" \
  PYTHONNOUSERSITE=1 \
  PIP_USER=0 \
  PYTHONPATH="${project_root}/src:${sdk_root}"
conda run -n "${environment_name}" --no-capture-output \
  python -m pip install --no-user "${wheel_path}"
# cyclonedds must resolve inside the disposable prefix; anywhere else means the
# shared system or user Python was modified and the change outlives the session.
conda run -n "${environment_name}" --no-capture-output python -c \
  'import sys, cyclonedds; raise SystemExit(0 if cyclonedds.__file__.startswith(sys.prefix) else 2)' ||
  fail "cyclonedds resolved outside ${environment_name}; do not install into system Python"
conda run -n "${environment_name}" --no-capture-output \
  python -m summit_signal.sdk_probe

setup_complete=true
trap - EXIT INT TERM HUP
if compgen -G "${HOME}/.local/lib/python3.10/site-packages/cyclonedds*" >/dev/null; then
  echo "WARNING: cyclonedds is also present in ~/.local/lib/python3.10/site-packages;" >&2
  echo "         that copy outlives this session. Remove it or tell the admin." >&2
fi

echo "JETSON SETUP COMPLETE: disposable environment ${environment_name} is ready."
echo "Next: ./scripts/jetson_prepare.sh"
