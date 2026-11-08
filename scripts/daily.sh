#!/usr/bin/env bash
# Procedura quotidiana (da cron). Uso: scripts/daily.sh run|refresh|verify
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
exec python3 -m fbi "${1:-run}" >> data/logs/cron.log 2>&1
