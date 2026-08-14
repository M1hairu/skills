#!/usr/bin/env bash
# Проверка решений ночного хука: что он пропускает и что отклоняет.
#
#   ./tests/run.sh        — прогнать
#
# Гоняется из scripts/validate.sh. Список автономных каталогов подсовывается
# временный, поэтому проверка не зависит от того, включён ли режим на машине.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/hook-cases.py" "$HERE/../bin/afk-autonomy"
