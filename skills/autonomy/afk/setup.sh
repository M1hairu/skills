#!/usr/bin/env bash
# Разовая настройка: в каком терминале открывать окно с ночной работой.
#
#   ./setup.sh                     — спросить и записать
#   SKILLS_ASSUME_YES=1 ./setup.sh — без вопросов, берётся опознанный терминал
#
# Запускает установщик после установки скилла. Ответ ложится в
# ~/.config/claude-skills/afk.env, где живут личные настройки остальных скиллов.
# Нужен потому, что сторож поднимает прерванную ночную работу в окне tmux, и это
# окно надо открыть человеку на глаза — иначе утром он смотрит в тот терминал,
# из которого сессия ушла, и думает, что всё пропало.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/claude-skills/afk.env"

# shellcheck disable=SC1091
. "$HERE/lib/terminal.sh"

if [[ -f "$CONF" ]] && grep -q '^AFK_TERMINAL=' "$CONF"; then
  printf '  · терминал уже выбран: %s\n' \
    "$(sed -n 's/^AFK_TERMINAL=//p' "$CONF" | tr -d '"' | tail -1)"
  exit 0
fi

TERMINAL="$(afk_detect_terminal || true)"

if [[ -z "$TERMINAL" ]]; then
  printf '  ! терминал не опознан — окно с ночной работой открывать нечем\n'
  printf '    задай сам: mkdir -p %s && echo '\''AFK_TERMINAL="kitty -e"'\'' >> %s\n' \
    "$(dirname "$CONF")" "$CONF"
  exit 0
fi

if [[ "${SKILLS_ASSUME_YES:-0}" != 1 && -t 0 ]]; then
  printf '  утром открывать ночную работу в «%s»? [Y/n] ' "${TERMINAL%% *}"
  read -r answer || answer=""
  if [[ "$answer" =~ ^[NnНн] ]]; then
    printf '    команда своего терминала (например «foot -e»): '
    read -r custom || custom=""
    [[ -n "$custom" ]] && TERMINAL="$custom"
  fi
fi

mkdir -p "$(dirname "$CONF")"
printf 'AFK_TERMINAL="%s"\n' "$TERMINAL" >> "$CONF"
chmod 600 "$CONF"
printf '  ✓ окно с ночной работой будет открываться в «%s»\n' "${TERMINAL%% *}"
