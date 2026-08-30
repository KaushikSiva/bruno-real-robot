#!/usr/bin/env bash

# MuJoCo's mjpython launcher needs a framework-enabled CPython on macOS.
# uv's managed standalone CPython does not include the required shared library.
select_mac_python() {
  local -a candidates=()
  if [[ -n "${SUMMIT_SIGNAL_MAC_PYTHON:-}" ]]; then
    candidates+=("${SUMMIT_SIGNAL_MAC_PYTHON}")
  else
    candidates+=(
      /opt/homebrew/bin/python3.12
      /usr/local/bin/python3.12
      python3.12
      python3.11
      python3.13
      python3.10
    )
  fi

  local candidate resolved
  for candidate in "${candidates[@]}"; do
    if [[ "${candidate}" == */* ]]; then
      resolved="${candidate}"
    else
      resolved="$(command -v "${candidate}" 2>/dev/null || true)"
    fi
    [[ -n "${resolved}" && -x "${resolved}" ]] || continue
    if "${resolved}" -c \
      'import sys,sysconfig; raise SystemExit(not ((3, 10) <= sys.version_info[:2] < (3, 14) and sysconfig.get_config_var("PYTHONFRAMEWORK") == "Python"))' \
      >/dev/null 2>&1; then
      printf '%s\n' "${resolved}"
      return 0
    fi
  done

  echo "No compatible macOS framework Python found (need Python 3.10-3.13)." >&2
  echo "Install Homebrew Python 3.12, or set SUMMIT_SIGNAL_MAC_PYTHON to one." >&2
  return 2
}
