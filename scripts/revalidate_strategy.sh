#!/usr/bin/env bash
# Годовая ре-валидация стратегии: обновить данные и перепрогнать исследование.
# Зачем: пресеты и правила (50/30/20, откуп при −30%) — не в граните. Раз в год
# (или после резкого изменения режима рынка) стоит проверить, что они ещё живы.
#
# Установка: crontab -e → например, 15 января каждого года:
#   0 9 15 1 * /home/pavel/Documents/work/invest_helper/scripts/revalidate_strategy.sh
#
# Результат: research/REPORT.md + research/data/factor_tables.md (обновляются
# in place). Ручная проверка: сверить выводы раздела «Выводы» с текущими
# пресетами в src/profile.py (STRATEGY_PRESETS) и порогами в src/risk.py.
set -uo pipefail
cd "$(dirname "$0")/.."
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ре-валидация стратегии ==="
  .venv/bin/python research/collect_data.py --corp RUCBITR
  .venv/bin/python research/factors.py
  .venv/bin/python research/backtest.py
  echo "=== готово: research/REPORT.md обновлён ==="
} >> research/revalidation.log 2>&1
