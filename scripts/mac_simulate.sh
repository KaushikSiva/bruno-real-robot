#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "SIMULATION FAILED: $*" >&2
  exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
source "${script_dir}/lib/mac_python.sh"
mac_python="$(select_mac_python)" || fail "MuJoCo requires a framework-enabled Python"
model_repo="${SUMMIT_SIGNAL_MODEL_REPO:-${project_root}/../unitree_mujoco}"
model_scene="${model_repo}/unitree_robots/g1/scene_29dof.xml"
calibration="${project_root}/runtime/dualsense-arm.json"
run_id="$(date -u '+%Y%m%dT%H%M%SZ')"
goals="${project_root}/runtime/simulation-goals-${run_id}.ndjson"
initial_state="${project_root}/runtime/simulation-initial-state-${run_id}.json"

[[ -f "${calibration}" ]] || fail "run scripts/mac_calibrate.sh first"
[[ -f "${model_scene}" ]] || fail "run scripts/mac_setup.sh first"

echo "Test one tiny movement: hold L1, move only the left stick vertically, center it,"
echo "release L1, verify Circle alone does not stop, then hold R2 and press Circle."
read -r -p "Enter S to open MuJoCo: " confirmation
[[ "${confirmation}" == "S" ]] || fail "simulation was not confirmed"

uv run --project "${project_root}" --python "${mac_python}" mjpython -m summit_signal.simulator \
  --model "${model_scene}" \
  --hardware-config "${project_root}/config/hardware.example.json" \
  --calibration "${calibration}" \
  --joystick-index 0 \
  --record-goals "${goals}" \
  --record-initial-state "${initial_state}" \
  --seconds 30

uv run --project "${project_root}" --python "${mac_python}" python -c \
  'import json,sys; rows=[json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]; raise SystemExit(0 if any(row["emergency_stop"] and row["connected"] for row in rows) else "R2+Circle was not observed; repeat simulation")' \
  "${goals}"

printf '%s\n' "simulation_passed_at=${run_id}" >"${project_root}/runtime/SIMULATION_PASSED"
echo "SIMULATION COMPLETE: R2+Circle and the commissioning gesture were recorded."
echo "Next on Jetson: ./scripts/jetson_setup.sh"
