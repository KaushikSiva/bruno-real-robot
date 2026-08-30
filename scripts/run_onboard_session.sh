#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "SESSION REFUSED: $*" >&2
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

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
export PYTHONPATH="${project_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
runtime_dir="${project_root}/runtime"
pid_file="${runtime_dir}/onboard.pid"
mkdir -p "${runtime_dir}"
umask 077
if [[ -s "${pid_file}" ]]; then
  existing_pid="$(<"${pid_file}")"
  if [[ "${existing_pid}" =~ ^[0-9]+$ ]] && kill -0 "${existing_pid}" 2>/dev/null; then
    fail "another onboard controller is recorded at PID ${existing_pid}"
  fi
fi

restore_normal() {
  local session_status=$?
  trap - EXIT INT TERM HUP
  rm -f -- "${pid_file}"
  echo "Restoring the facility's built-in controller with: robot normal" >&2
  if ! robot normal; then
    echo "CRITICAL: robot normal failed; contact the admin immediately." >&2
    exit 4
  fi
  robot status || true
  exit "${session_status}"
}
trap restore_normal EXIT

child_pid=0
forward_signal() {
  local signal_name=$1
  local signal_code=$2
  trap - INT TERM HUP
  if [[ "${child_pid}" -gt 0 ]]; then
    kill "-${signal_name}" "${child_pid}" 2>/dev/null || true
    wait "${child_pid}" 2>/dev/null || true
  fi
  exit "${signal_code}"
}
trap 'forward_signal INT 130' INT
trap 'forward_signal TERM 143' TERM
trap 'forward_signal TERM 129' HUP

python -m summit_signal.onboard "$@" <&0 &
child_pid=$!
printf '%s\n' "${child_pid}" >"${pid_file}"
echo "Onboard controller PID: ${child_pid}" >&2
echo "Separate-terminal stop: scripts/stop_onboard.sh" >&2
wait "${child_pid}"
