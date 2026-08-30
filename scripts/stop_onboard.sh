#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
pid_file="${project_root}/runtime/onboard.pid"

if [[ ! -s "${pid_file}" ]]; then
  echo "No recorded onboard controller is running." >&2
  exit 1
fi

controller_pid="$(<"${pid_file}")"
if [[ ! "${controller_pid}" =~ ^[0-9]+$ ]]; then
  echo "Refusing invalid controller PID file." >&2
  exit 2
fi
if ! kill -0 "${controller_pid}" 2>/dev/null; then
  echo "Recorded controller PID is no longer running." >&2
  exit 1
fi
controller_command="$(ps -p "${controller_pid}" -o command=)"
if [[ "${controller_command}" != *"summit_signal.onboard"* ]]; then
  echo "PID ${controller_pid} is not the Summit Signal onboard controller; refusing." >&2
  exit 2
fi

kill -TERM "${controller_pid}"
echo "SIGTERM sent to onboard controller PID ${controller_pid}."
echo "Wait for ALL-29 DAMPING SHUTDOWN COMPLETE and robot normal."
