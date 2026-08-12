#!/usr/bin/env bash
# Установка скиллов из этого репозитория симлинками.
#
#   ./install.sh                  — поставить всё
#   ./install.sh afk              — поставить только названные скиллы
#   ./install.sh --dry-run        — показать, что произойдёт, и ничего не делать
#   ./install.sh --force          — заменить мешающие реальные файлы (с бэкапом)
#   ./install.sh --uninstall      — снять симлинки, ведущие в этот репозиторий
#   ./install.sh --list           — перечислить скиллы репозитория
#
# Ставится симлинками, а не копиями: правка файла в репозитории действует
# сразу, без переустановки.
#
# Что куда идёт (соглашение, новые скиллы ничего здесь не меняют):
#
#   skills/<имя>/SKILL.md   → сам скилл, каталог линкуется в ~/.claude/skills/<имя>
#   skills/<имя>/bin/*      → команды, линкуются по одной в ~/.local/bin/
#   skills/<имя>/claude/*   → файлы окружения Claude, линкуются в ~/.claude/

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$REPO/skills"
DEST_SKILLS="${CLAUDE_HOME:-$HOME/.claude}/skills"
DEST_CLAUDE="${CLAUDE_HOME:-$HOME/.claude}"
DEST_BIN="$HOME/.local/bin"
# Бэкапы лежат в стороне: каталог с бэкапом внутри ~/.claude/skills Claude Code
# принял бы за ещё один скилл, и рядом с afk появился бы двойник afk.backup-…
BACKUP_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/claude-skills/backups"

DRY=0
FORCE=0
UNINSTALL=0
LIST=0
WANTED=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) DRY=1; shift ;;
    --force|-f)   FORCE=1; shift ;;
    --uninstall)  UNINSTALL=1; shift ;;
    --list|-l)    LIST=1; shift ;;
    -h|--help)    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)           echo "install: неизвестный флаг $1" >&2; exit 2 ;;
    *)            WANTED+=("$1"); shift ;;
  esac
done

# ── вывод ──
c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
[[ -t 1 ]] || { c_ok=""; c_warn=""; c_dim=""; c_off=""; }

linked=0; skipped=0; conflicts=0; removed=0
RUN_STAMP="$(date +%Y%m%d-%H%M%S)"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$c_ok" "$c_off" "$*"; }
warn() { printf '  %s!%s %s\n' "$c_warn" "$c_off" "$*"; }
dim()  { printf '  %s·%s %s\n' "$c_dim" "$c_off" "$*"; }

# Список скиллов: каталог с SKILL.md внутри.
all_skills() {
  local d
  for d in "$SKILLS_DIR"/*/; do
    [[ -f "$d/SKILL.md" ]] && basename "$d"
  done
}

# ── одна связь: link <источник в репо> <место назначения> ──
link() {
  local src="$1" dst="$2" rel="${2/#$HOME/\~}"

  if [[ -L "$dst" ]]; then
    local cur; cur="$(readlink -f "$dst" 2>/dev/null || true)"
    if [[ "$cur" == "$(readlink -f "$src")" ]]; then
      dim "$rel — уже стоит"; skipped=$((skipped + 1)); return
    fi
    if [[ "$cur" == "$REPO"/* ]]; then
      (( DRY )) || { rm "$dst"; ln -s "$src" "$dst"; }
      ok "$rel — переставлен"; linked=$((linked + 1)); return
    fi
    if (( FORCE )); then
      (( DRY )) || { rm "$dst"; ln -s "$src" "$dst"; }
      ok "$rel — заменён чужой симлинк"; linked=$((linked + 1)); return
    fi
    warn "$rel — уже симлинк наружу ($cur), пропускаю; --force заменит"
    conflicts=$((conflicts + 1)); return
  fi

  if [[ -e "$dst" ]]; then
    if (( FORCE )); then
      local bak="$BACKUP_ROOT/$RUN_STAMP/${dst#$HOME/}"
      (( DRY )) || { mkdir -p "$(dirname "$bak")"; mv "$dst" "$bak"; ln -s "$src" "$dst"; }
      ok "$rel — заменён, старое в ${bak/#$HOME/\~}"; linked=$((linked + 1)); return
    fi
    warn "$rel — существует и это не симлинк, пропускаю; --force заменит с бэкапом"
    conflicts=$((conflicts + 1)); return
  fi

  (( DRY )) || { mkdir -p "$(dirname "$dst")"; ln -s "$src" "$dst"; }
  ok "$rel"; linked=$((linked + 1))
}

# ── снятие: удаляем только то, что ведёт в этот репозиторий ──
unlink_if_ours() {
  local dst="$1" rel="${1/#$HOME/\~}"
  [[ -L "$dst" ]] || return 0
  local cur; cur="$(readlink -f "$dst" 2>/dev/null || true)"
  [[ "$cur" == "$REPO"/* ]] || return 0
  (( DRY )) || rm "$dst"
  ok "снят $rel"; removed=$((removed + 1))
}

install_skill() {
  local name="$1" dir="$SKILLS_DIR/$1" f
  say "$name"
  link "$dir" "$DEST_SKILLS/$name"

  if [[ -d "$dir/bin" ]]; then
    for f in "$dir"/bin/*; do
      [[ -f "$f" ]] || continue
      chmod +x "$f" 2>/dev/null || true
      link "$f" "$DEST_BIN/$(basename "$f")"
    done
  fi

  if [[ -d "$dir/claude" ]]; then
    for f in "$dir"/claude/*; do
      [[ -e "$f" ]] || continue
      link "$f" "$DEST_CLAUDE/$(basename "$f")"
    done
  fi
}

uninstall_skill() {
  local name="$1" dir="$SKILLS_DIR/$1" f
  say "$name"
  unlink_if_ours "$DEST_SKILLS/$name"
  [[ -d "$dir/bin" ]] && for f in "$dir"/bin/*; do
    [[ -f "$f" ]] && unlink_if_ours "$DEST_BIN/$(basename "$f")"
  done
  [[ -d "$dir/claude" ]] && for f in "$dir"/claude/*; do
    [[ -e "$f" ]] && unlink_if_ours "$DEST_CLAUDE/$(basename "$f")"
  done
  return 0
}

# ── что ставим ──
mapfile -t AVAILABLE < <(all_skills)
if [[ ${#AVAILABLE[@]} -eq 0 ]]; then
  echo "install: в $SKILLS_DIR нет ни одного скилла" >&2
  exit 1
fi

if (( LIST )); then
  for s in "${AVAILABLE[@]}"; do
    desc=$(sed -n 's/^description: *//p' "$SKILLS_DIR/$s/SKILL.md" | head -1 | cut -c1-90)
    printf '%-12s %s\n' "$s" "$desc"
  done
  exit 0
fi

TARGETS=()
if [[ ${#WANTED[@]} -gt 0 ]]; then
  for w in "${WANTED[@]}"; do
    if [[ -f "$SKILLS_DIR/$w/SKILL.md" ]]; then
      TARGETS+=("$w")
    else
      echo "install: нет такого скилла — $w (см. ./install.sh --list)" >&2
      exit 1
    fi
  done
else
  TARGETS=("${AVAILABLE[@]}")
fi

(( DRY )) && say "${c_dim}пробный прогон, ничего не меняется${c_off}" && say ""

if (( UNINSTALL )); then
  for s in "${TARGETS[@]}"; do uninstall_skill "$s"; say ""; done
  say "снято: $removed"
  exit 0
fi

(( DRY )) || mkdir -p "$DEST_SKILLS" "$DEST_BIN"

for s in "${TARGETS[@]}"; do install_skill "$s"; say ""; done

summary="поставлено: $linked, уже было: $skipped"
(( conflicts > 0 )) && summary="$summary, пропущено из-за конфликта: $conflicts"
say "$summary"

# ── послесловие ──
case ":$PATH:" in
  *":$DEST_BIN:"*) ;;
  *) say ""
     warn "$DEST_BIN не в \$PATH — команды скиллов не запустятся"
     say "     добавь в ~/.zshrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

for s in "${TARGETS[@]}"; do
  setup="$SKILLS_DIR/$s/SETUP.md"
  if [[ -f "$setup" ]]; then
    say ""
    say "$s требует настройки — см. skills/$s/SETUP.md"
  fi
done

(( conflicts > 0 )) && exit 3
exit 0
