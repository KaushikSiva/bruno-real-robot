#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "JETSON CLEANUP REFUSED: $*" >&2
  exit 2
}

environment_name="${1:-}"
[[ "${environment_name}" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] ||
  fail "usage: $0 YOURNAME-g1"
[[ "${environment_name}" != "base" ]] || fail "refusing to remove the shared base environment"
[[ "${CONDA_DEFAULT_ENV:-}" != "${environment_name}" ]] ||
  fail "run 'conda deactivate' before cleanup"
command -v conda >/dev/null || fail "conda is unavailable"
command -v robot >/dev/null || fail "the facility robot helper is unavailable"
if ! conda env list | awk -v target="${environment_name}" '$1 == target { found=1 } END { exit(found ? 0 : 1) }'; then
  fail "conda environment ${environment_name} does not exist"
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
pid_file="${project_root}/runtime/onboard.pid"

if [[ -s "${pid_file}" ]]; then
  controller_pid="$(<"${pid_file}")"
  if [[ "${controller_pid}" =~ ^[0-9]+$ ]] && kill -0 "${controller_pid}" 2>/dev/null; then
    fail "controller PID ${controller_pid} is still running; use scripts/stop_onboard.sh first"
  fi
fi
if command -v pgrep >/dev/null && pgrep -f '[s]ummit_signal.onboard' >/dev/null; then
  fail "a Summit Signal onboard controller is still running; stop it before cleanup"
fi

read -r -p "Type ROBOT-SAFE after checking the live camera: " confirmation
[[ "${confirmation}" == "ROBOT-SAFE" ]] || fail "final physical state was not confirmed"

robot normal
robot status || fail "final robot status failed; contact the admin immediately"
read -r -p "Type NORMAL-MODE-SAFE after rechecking the live camera: " confirmation
[[ "${confirmation}" == "NORMAL-MODE-SAFE" ]] ||
  fail "normal mode was not visually confirmed; contact the admin"
conda env remove -n "${environment_name}" -y
rm -f -- "${project_root}/runtime/hardware.json" "${pid_file}"
rmdir "${project_root}/runtime" 2>/dev/null || true

echo "JETSON CLEANUP COMPLETE: ${environment_name} and session runtime files were removed."
echo "Delete the checkout too if the facility asks; no services or autostart entries were created."
