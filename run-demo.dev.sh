#!/usr/bin/env bash
set -euo pipefail

# Git Bash entry point for the PowerShell demo launcher.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
launch_args=()
while (($#)); do
  case "$1" in
    --stop) launch_args+=(-Stop); shift ;;
    --port|--api-port)
      if (($# < 2)) || [[ ! "$2" =~ ^[0-9]+$ ]]; then
        printf '%s requires a numeric port.\n' "$1" >&2
        exit 2
      fi
      if [[ "$1" == --port ]]; then
        launch_args+=(-Port "$2")
      else
        launch_args+=(-ApiPort "$2")
      fi
      shift 2
      ;;
    -h|--help)
      printf '%s\n' \
        'Usage: bash run-demo.dev.sh [--port 8765] [--api-port 8766] [--stop]' \
        'Starts the local demo with live updates. Ctrl+C stops both services.' \
        'Restarts recognised demo processes. Refuses to stop unrelated processes.' \
        'Requires Git Bash on Windows, Node.js and uv.'
      exit 0
      ;;
    *) printf 'Unknown option: %s. Use --help.\n' "$1" >&2; exit 2 ;;
  esac
done

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) printf '%s\n' 'Use Git Bash on Windows. This demo uses Windows paths and process checks.' >&2; exit 1 ;;
esac
if ! command -v powershell.exe >/dev/null 2>&1; then
  printf '%s\n' 'Windows PowerShell was not found on PATH.' >&2
  exit 1
fi
launcher_path="$(cygpath -w "$script_dir/run-demo.dev.ps1")"
# Pass literal Windows paths and preserve Ctrl+C/exit status through the helper.
export MSYS2_ARG_CONV_EXCL='*'
# PowerShell 7 may have supplied incompatible module paths to Git Bash.
# Let Windows PowerShell initialise its own defaults in this child process.
unset PSModulePath
exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$launcher_path" "${launch_args[@]}"
