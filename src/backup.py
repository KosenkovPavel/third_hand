"""Ротационные бэкапы файлов данных: копия в data/backups/ перед перезаписью."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

DEFAULT_KEEP = 10


def backup_file(path: Path, keep: int = DEFAULT_KEEP) -> Path | None:
    """Копирует файл в data/backups/<имя>.<timestamp><ext>, хранит последние ``keep``.

    Возвращает путь копии или None, если исходного файла нет.
    """
    keep = max(int(keep), 1)
    path = Path(path)
    if not path.exists():
        return None
    backups = path.parent / "backups"
    backups.mkdir(exist_ok=True)
    dest = backups / f"{path.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{path.suffix}"
    shutil.copy2(path, dest)

    old = sorted(backups.glob(f"{path.stem}.*{path.suffix}"))
    for extra in old[:-keep]:
        try:
            extra.unlink()
        except OSError:
            pass
    return dest
