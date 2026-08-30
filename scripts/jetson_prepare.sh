#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "JETSON PREPARATION FAILED: $*" >&2
  exit 2
}

environment_name="${1:-}"
[[ "${environment_name}" =~ ^[A-Za-z][A-Za-z0-9_-]{2,31}$ ]] ||
  fail "usage: $0 YOURNAME-g1"
command -v conda >/dev/null || fail "conda is unavailable"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
cd "${project_root}"

git diff --quiet && git diff --cached --quiet ||
  fail "tracked repository files are modified; use the exact tested checkout"
read -r -p "Type RULES-ACCESS-SIM-CONFIRMED after reading the rules and passing Mac simulation: " confirmation
[[ "${confirmation}" == "RULES-ACCESS-SIM-CONFIRMED" ]] ||
  fail "rules, personal access, and simulation were not confirmed"

PYTHONPATH="${project_root}/src${PYTHONPATH:+:${PYTHONPATH}}" \
  conda run -n "${environment_name}" --no-capture-output \
  python -m summit_signal.activation \
    --facility-rules-acknowledged \
    --personal-access-confirmed \
    --simulation-passed

echo "JETSON PREPARATION COMPLETE."
echo "Next: ./scripts/jetson_preflight.sh ${environment_name}"
