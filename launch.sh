#!/usr/bin/env bash
# bash launch.sh <stage>   (see AGENT_INSTRUCTIONS.md)
set -euo pipefail
cd "$(dirname "$0")"
exec python3 ops.py launch "$@"
