#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "PREFLIGHT FAILED: $*" >&2
  exit 2
}

if [[ -z "${CONDA_PREFIX:-}" || -z "${CONDA_DEFAULT_ENV:-}" ]]; then
  fail "activate your disposable conda environment first"
fi
if [[ "${CONDA_DEFAULT_ENV}" == "base" ]]; then
  fail "the shared base environment is forbidden; activate your own environment"
fi

python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 10) else 2)' ||
  fail "the active conda environment must use Python 3.10"
python -c 'import cyclonedds, unitree_sdk2py' ||
  fail "cyclonedds or the facility-provided Unitree SDK is unavailable"
command -v robot >/dev/null || fail "the facility robot helper is unavailable"

echo "Current robot ownership/status:"
robot status
echo
echo "Before continuing: confirm exclusive access, on-site hours, a live camera,"
echo "and that the admin killswitch is ready."
read -r -p "Type CAMERA-KILLSWITCH-READY to enter developer mode: " confirmation
[[ "${confirmation}" == "CAMERA-KILLSWITCH-READY" ]] ||
  fail "camera/killswitch confirmation was not supplied"

restore_normal=true
restore_on_failure() {
  if [[ "${restore_normal}" == "true" ]]; then
    echo "Preflight did not complete; restoring the built-in controller." >&2
    robot normal || true
  fi
}
trap restore_on_failure EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

echo "Requesting zero torque through the built-in service before developer mode."
robot zero
robot dev-mode
echo "Developer mode requested. Check the live camera now."
read -r -p "Type FACE-GREEN only after the face light is green: " confirmation
[[ "${confirmation}" == "FACE-GREEN" ]] ||
  fail "developer mode was not visually confirmed"
robot status

restore_normal=false
trap - EXIT INT TERM HUP
echo "PREFLIGHT COMPLETE. Start the onboard controller immediately."
