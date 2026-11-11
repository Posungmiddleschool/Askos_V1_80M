#!/usr/bin/env bash
# bash stop.sh <stage>   graceful stop of this account's run (see AGENT_INSTRUCTIONS.md)
set -euo pipefail
cd "$(dirname "$0")"
exec python3 ops.py stop "$@"
