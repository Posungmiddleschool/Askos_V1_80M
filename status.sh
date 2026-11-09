#!/usr/bin/env bash
# bash status.sh <stage>   (see AGENT_INSTRUCTIONS.md)
set -euo pipefail
cd "$(dirname "$0")"
exec python3 ops.py status "$@"
