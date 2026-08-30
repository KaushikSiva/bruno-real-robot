#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "CALIBRATION FAILED: $*" >&2
  exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
calibration="${project_root}/runtime/dualsense-arm.json"

command -v uv >/dev/null || fail "run scripts/mac_setup.sh first"
mkdir -p "${project_root}/runtime"

if [[ -e "${calibration}" ]]; then
  read -r -p "Calibration exists. Type RECALIBRATE to replace it: " confirmation
  [[ "${confirmation}" == "RECALIBRATE" ]] || fail "existing calibration left unchanged"
fi

uv run --project "${project_root}" --python 3.10 summit-signal-operator --list-joysticks
uv run --project "${project_root}" --python 3.10 summit-signal-operator \
  --calibrate "${calibration}" \
  --joystick-index 0

echo "CALIBRATION COMPLETE: ${calibration}"
echo "Next: ./scripts/mac_simulate.sh"
