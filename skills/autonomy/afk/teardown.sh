#!/usr/bin/env bash
# Что убрать за скиллом при его удалении.
#
#   ./teardown.sh
#
# Запускает установщик перед снятием симлинков. Нужен потому, что скилл меняет
# по ходу работы то, о чём установщик не знает: список автономных каталогов,
# хук в `settings.local.json` и настройку изоляции фоновых сессий. Без этого
# после удаления скилла в настройках остаётся хук, зовущий несуществующую
# команду, и молча выключенная изоляция.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -x "$HERE/bin/afk-autonomy" ]]; then
  "$HERE/bin/afk-autonomy" --off --all || true
fi

# Сторожа, оставшиеся от прошлых ночей, тоже снимаем: юнит переживёт удаление
# скилла и будет пытаться поднимать работу командой, которой уже нет.
while read -r unit; do
  [[ -n "$unit" ]] || continue
  systemctl --user stop "$unit" 2>/dev/null || true
  printf '  · снят сторож %s\n' "$unit"
done < <(systemctl --user list-units 'afk-watch-*' --no-legend --plain 2>/dev/null | awk '{print $1}')
