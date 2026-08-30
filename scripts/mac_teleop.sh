#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "TELEOP REFUSED: $*" >&2
  exit 2
}

robot_host="${1:-}"
environment_name="${2:-kaushik-g1}"
[[ "${robot_host}" =~ ^[A-Za-z0-9_.@:-]+$ ]] || fail "usage: $0 ROBOT_SSH_HOST [ENVIRONMENT_NAME]"
[[ "${environment_name}" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] ||
  fail "usage: $0 ROBOT_SSH_HOST [ENVIRONMENT_NAME] (default: kaushik-g1)"
command -v tailscale >/dev/null || fail "Tailscale CLI is unavailable"
command -v ssh >/dev/null || fail "ssh is unavailable"
command -v open >/dev/null || fail "this operator script expects macOS"
command -v tee >/dev/null || fail "tee is unavailable"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
source "${script_dir}/lib/mac_python.sh"
mac_python="$(select_mac_python)" || fail "MuJoCo requires a framework-enabled Python"
calibration="${project_root}/runtime/dualsense-arm.json"
simulation_marker="${project_root}/runtime/SIMULATION_PASSED"
seconds="${TELEOP_SECONDS:-30}"

[[ -f "${calibration}" ]] || fail "run scripts/mac_calibrate.sh first"
[[ -f "${simulation_marker}" ]] || fail "run scripts/mac_simulate.sh and pass R2+Circle first"
[[ "${seconds}" =~ ^[0-9]+$ ]] && ((seconds >= 5 && seconds <= 120)) ||
  fail "TELEOP_SECONDS must be an integer from 5 to 120"

tailscale status >/dev/null || fail "Tailscale is not connected"
camera_url="${ROBOT_CAMERA_URL:-}"
if [[ -z "${camera_url}" ]]; then
  read -r -s -p "Paste the facility camera URL (hidden and not saved): " camera_url
  echo
fi
[[ "${camera_url}" == https://* ]] || fail "camera URL must use HTTPS"
open "${camera_url}"

echo "Before continuing, confirm the camera is live, the face light is green,"
echo "the preflight completed, L1 is released, and both sticks are centered."
read -r -p "Type PREFLIGHT-GREEN-CAMERA-LIVE: " confirmation
[[ "${confirmation}" == "PREFLIGHT-GREEN-CAMERA-LIVE" ]] ||
  fail "live camera and developer mode were not confirmed"

umask 077
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
log_dir="${project_root}/runtime/hardware-logs"
goal_log="${log_dir}/goals-${run_id}.ndjson"
session_log="${log_dir}/session-${run_id}.log"
mkdir -p -- "${log_dir}"
chmod 700 -- "${log_dir}"

report_logs() {
  echo "Private Mac logs saved:" >&2
  echo "  goals:  ${goal_log}" >&2
  echo "  session: ${session_log}" >&2
}
trap report_logs EXIT

# Non-interactive SSH sessions do not source the Jetson's interactive shell
# setup, so `conda` is not normally added to PATH. Use the installation path
# verified during Jetson setup instead of relying on shell initialization.
remote_command="cd bruno-real-robot && /home/unitree/miniconda3/bin/conda run -n ${environment_name} --no-capture-output ./scripts/run_onboard_session.sh --config runtime/hardware.json --motion-profile commissioning --real-robot --facility-rules-acknowledged --exclusive-access-confirmed --within-onsite-hours-confirmed --camera-confirmed --developer-mode-confirmed"

uv run --project "${project_root}" --python "${mac_python}" summit-signal-operator \
  --calibration "${calibration}" \
  --joystick-index 0 \
  --seconds "${seconds}" |
  tee "${goal_log}" |
  ssh -T "${robot_host}" "${remote_command}" 2>&1 |
  tee "${session_log}"
