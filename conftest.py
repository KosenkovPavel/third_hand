"""Настройка pytest: подключаем вендоренный SDK Т-Инвестиций.

SDK `t-tech-investments` не публикуется на PyPI (раздаётся через портал Т-Банка,
недоступный из CI), поэтому лежит в `vendor/t_tech`. Добавляем `vendor` в sys.path,
чтобы `import t_tech` работал и в тестах, и в GitHub Actions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vendor"))
