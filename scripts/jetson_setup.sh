#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "JETSON SETUP FAILED: $*" >&2
  exit 2
}

environment_name="${1:-}"
[[ "${environment_name}" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] ||
  fail "usage: $0 YOURNAME-g1 (3-32 safe characters, beginning with a letter)"
[[ "${environment_name}" != "base" ]] || fail "the shared base environment is forbidden"
command -v conda >/dev/null || fail "conda is unavailable"
# The facility builds and caches the cyclonedds wheel against this prefix. Without it
# pip falls back to a long source compile against the shared system install.
[[ -n "${CYCLONEDDS_HOME:-}" ]] ||
  fail "CYCLONEDDS_HOME is unset; the facility normally sets it. Contact the admin"
[[ -d "${CYCLONEDDS_HOME}" ]] ||
  fail "CYCLONEDDS_HOME=${CYCLONEDDS_HOME} is not a directory"
# Never let a stray --user default drop packages into ~/.local: that survives
# conda env remove and changes the shared robot for the next operator.
export PYTHONNOUSERSITE=1
export PIP_USER=0
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"

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
conda run -n "${environment_name}" --no-capture-output \
  python -m pip install --no-user cyclonedds==0.10.2
# cyclonedds must resolve inside the disposable prefix; anywhere else means the
# shared system or user Python was modified and the change outlives the session.
conda run -n "${environment_name}" --no-capture-output python -c \
  'import sys, cyclonedds; raise SystemExit(0 if cyclonedds.__file__.startswith(sys.prefix) else 2)' ||
  fail "cyclonedds resolved outside ${environment_name}; do not install into system Python"
PYTHONPATH="${project_root}/src${PYTHONPATH:+:${PYTHONPATH}}" \
  conda run -n "${environment_name}" --no-capture-output \
  python -m summit_signal.sdk_probe

setup_complete=true
trap - EXIT INT TERM HUP
if compgen -G "${HOME}/.local/lib/python3.10/site-packages/cyclonedds*" >/dev/null; then
  echo "WARNING: cyclonedds is also present in ~/.local/lib/python3.10/site-packages;" >&2
  echo "         that copy outlives this session. Remove it or tell the admin." >&2
fi

echo "JETSON SETUP COMPLETE: disposable environment ${environment_name} is ready."
echo "Next: ./scripts/jetson_prepare.sh ${environment_name}"
