#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "JETSON CLEANUP REFUSED: $*" >&2
  exit 2
}

environment_name="${1:-kaushik-g1}"
[[ "${environment_name}" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] ||
  fail "usage: $0 [ENVIRONMENT_NAME] (default: kaushik-g1)"
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

# Nothing may survive a power cycle. These scripts never create such entries, so a hit
# here means something else did and the next operator inherits it. Inspection only:
# no entry is created or modified by this sweep.
persistence_found=false
note_persistence() {
  persistence_found=true
  echo "WARNING: possible persistent entry -> $*" >&2
}

if command -v crontab >/dev/null; then
  while IFS= read -r entry; do
    [[ -n "${entry}" ]] && note_persistence "user cron: ${entry}"
  done < <(crontab -l 2>/dev/null | grep -viE '^[[:space:]]*#' |
    grep -iE 'summit[_-]signal|bruno-real-robot' || true)
fi

if command -v systemctl >/dev/null; then
  while IFS= read -r entry; do
    [[ -n "${entry}" ]] && note_persistence "user unit: ${entry}"
  done < <(systemctl --user list-unit-files --no-legend --no-pager 2>/dev/null |
    grep -iE 'summit[_-]signal|bruno' || true)
fi

for autostart_entry in "${HOME}/.config/autostart"/*.desktop; do
  [[ -e "${autostart_entry}" ]] || continue
  if grep -qiE 'summit[_-]signal|bruno-real-robot' "${autostart_entry}" 2>/dev/null; then
    note_persistence "autostart: ${autostart_entry}"
  fi
done

if compgen -G "${HOME}/.local/lib/python3.10/site-packages/cyclonedds*" >/dev/null; then
  note_persistence "cyclonedds in ~/.local/lib/python3.10/site-packages"
fi

# The Orin's disk is small and shared, so name what is still taking space.
if [[ -d "${project_root}/runtime" ]]; then
  echo
  echo "Session data still in ${project_root}/runtime ($(du -sh -- "${project_root}/runtime" 2>/dev/null | cut -f1)):"
  ls -lAh -- "${project_root}/runtime" || true
  echo "Delete any recordings you do not need; the Orin's disk is small and shared."
fi

if [[ "${persistence_found}" == "true" ]]; then
  echo
  echo "JETSON CLEANUP INCOMPLETE: ${environment_name} was removed, but the entries above"
  echo "would outlive this session. Remove them, or tell the admin."
else
  echo "JETSON CLEANUP COMPLETE: ${environment_name} and session runtime files were removed."
  echo "Nothing references this session in user cron, user units, or autostart."
fi
echo "Delete the checkout too if the facility asks; no services or autostart entries were created."
echo
echo "Last step: tell the admin about anything that felt off this session - an unexpected"
echo "noise, a fault, a stall, a vibration, a dropped connection - even if it resolved"
echo "itself. The next person is trusting you."
