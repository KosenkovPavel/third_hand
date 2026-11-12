#!/usr/bin/env bash
# Ежедневный снимок портфеля в data/history.json (для crontab).
# Установка: crontab -e → добавь строку вида
#   17 9 * * * /home/pavel/Documents/work/invest_helper/scripts/daily_snapshot.sh
set -uo pipefail
cd "$(dirname "$0")/.."
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  .venv/bin/python main.py --save
} >> data/cron.log 2>&1
