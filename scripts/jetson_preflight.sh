#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "PREFLIGHT FAILED: $*" >&2
  exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"

if [[ -z "${CONDA_PREFIX:-}" || -z "${CONDA_DEFAULT_ENV:-}" ]]; then
  environment_name="${1:-}"
  [[ "${environment_name}" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] ||
    fail "usage outside an active env: $0 YOURNAME-g1"
  [[ -z "${SUMMIT_SIGNAL_CONDA_REEXEC:-}" ]] ||
    fail "conda run did not activate the requested environment"
  command -v conda >/dev/null || fail "conda is unavailable"
  exec env SUMMIT_SIGNAL_CONDA_REEXEC=1 conda run -n "${environment_name}" \
    --no-capture-output "${script_dir}/jetson_preflight.sh"
fi
[[ $# -eq 0 ]] || fail "do not pass an environment name from inside an active environment"
if [[ "${CONDA_DEFAULT_ENV}" == "base" ]]; then
  fail "the shared base environment is forbidden; activate your own environment"
fi

export PYTHONPATH="${project_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
python -m summit_signal.sdk_probe || fail "the disposable SDK environment is incompatible"
# cyclonedds must live in this disposable prefix; anywhere else means the shared
# system or user Python was modified and the change would outlive the session.
python -c 'import sys, cyclonedds; raise SystemExit(0 if cyclonedds.__file__.startswith(sys.prefix) else 2)' ||
  fail "cyclonedds resolves outside ${CONDA_PREFIX}; install it only in your own environment"
command -v robot >/dev/null || fail "the facility robot helper is unavailable"

# --- developer-mode verification -------------------------------------------------
# Sending rt/lowcmd while the built-in motion service still owns the joints makes the
# two controllers fight over every joint and the robot vibrates violently. Ask the
# robot rather than trusting a flag typed once on the operator's command line.
# Override the match if the facility's `robot status` wording differs:
#   export SUMMIT_SIGNAL_DEV_MODE_PATTERN='exact text shown in developer mode'
dev_mode_pattern="${SUMMIT_SIGNAL_DEV_MODE_PATTERN:-dev[-_ ]?mode|developer}"

verify_developer_mode() {
  local status_output
  # </dev/null so this can never consume the operator goal stream on stdin.
  if ! status_output="$(robot status 2>&1 </dev/null)"; then
    fail "'robot status' failed; cannot confirm developer mode"
  fi
  echo "robot status:" >&2
  printf '%s\n' "${status_output}" >&2
  if ! printf '%s' "${status_output}" | grep -Eiq -- "${dev_mode_pattern}"; then
    echo "The status above does not match /${dev_mode_pattern}/i." >&2
    echo "If the built-in motion service still owns the robot, starting the" >&2
    echo "controller now would make the robot VIBRATE VIOLENTLY." >&2
    echo "Run ./scripts/jetson_preflight.sh, or if this wording is what your" >&2
    echo "facility prints in developer mode, set SUMMIT_SIGNAL_DEV_MODE_PATTERN." >&2
    fail "developer mode is not confirmed by 'robot status'"
  fi
}

utc_hour_text="$(date -u +%H)" || fail "cannot read the current UTC time"
utc_hour=$((10#${utc_hour_text}))
if ((utc_hour < 2 || utc_hour >= 9)); then
  fail "outside staffed hours (10:00-17:00 MYT / 02:00-09:00 UTC); current time: $(date -u '+%Y-%m-%d %H:%M UTC')"
fi

echo "Current robot ownership/status:"
robot status
echo
read -r -p "Type BUILTIN-OWNER-CONFIRMED only if the built-in service owns the robot: " confirmation
[[ "${confirmation}" == "BUILTIN-OWNER-CONFIRMED" ]] ||
  fail "built-in ownership was not confirmed; do not call robot zero"

echo "Before continuing: confirm exclusive access, on-site hours, a live camera,"
echo "and that the admin killswitch is ready."
read -r -p "Type EXCLUSIVE-CAMERA-KILLSWITCH-READY to enter developer mode: " confirmation
[[ "${confirmation}" == "EXCLUSIVE-CAMERA-KILLSWITCH-READY" ]] ||
  fail "exclusive access, camera, and killswitch readiness were not confirmed"

restore_normal=true
restore_on_failure() {
  if [[ "${restore_normal}" == "true" ]]; then
    echo "Preflight did not complete; restoring the built-in controller." >&2
    if ! robot normal; then
      echo "CRITICAL: robot normal failed; contact the admin immediately." >&2
    fi
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
verify_developer_mode

restore_normal=false
trap - EXIT INT TERM HUP
echo "PREFLIGHT COMPLETE. Start the onboard controller immediately."
echo
echo "The robot is now limp and resting in the harness; that is expected on the gantry."
echo "If the robot VIBRATES when the controller starts, the built-in motion service is"
echo "still fighting your commands: stop the controller immediately with Ctrl-C or"
echo "./scripts/stop_onboard.sh, then contact the admin. Do not retry blindly."
