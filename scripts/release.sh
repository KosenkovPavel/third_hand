#!/usr/bin/env bash
# Выпуск релиза: бампает версию, коммитит, ставит тег vX.Y.Z и пушит.
# Пуш тега запускает GitHub Actions → создаётся GitHub Release (без артефактов:
# их соберём после перехода на Rust).
#
# Использование:
#   ./scripts/release.sh 0.5.1
set -euo pipefail

VERSION="${1:?укажи версию, например: 0.5.1}"

cd "$(dirname "$0")/.."

# 1. Бамп версии в src/__init__.py
sed -i "s/^__version__ = .*/__version__ = \"${VERSION}\"/" src/__init__.py
grep -q "^__version__ = \"${VERSION}\"" src/__init__.py

# 2. Коммит + тег
git add src/__init__.py
git commit -m "chore: bump version to ${VERSION}"
git tag "v${VERSION}"

# 3. Пуш коммита в main и тега (тег запускает release-воркфлоу)
git push origin HEAD
git push origin "v${VERSION}"

echo "Готово. Тег v${VERSION} запушен — GitHub Actions создаст Release."
