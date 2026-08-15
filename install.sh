#!/usr/bin/env bash
# Установка скиллов из этого репозитория симлинками.
#
#   ./install.sh                  — поставить всё глобально, для всех проектов
#   ./install.sh afk vps-afk      — поставить только названные
#   ./install.sh -i               — выбрать из списка руками
#   ./install.sh --list           — перечислить скиллы репозитория
#
#   --local                       — поставить в текущий проект, а не глобально
#   --project <путь>              — то же, но проект указан явно
#   --agents                      — заодно в ~/.agents/skills (Codex и прочие харнессы)
#
#   --dry-run                     — показать, что произойдёт, и ничего не делать
#   --force                       — заменить мешающие реальные файлы (старое в бэкап)
#   --uninstall                   — снять симлинки, ведущие в этот репозиторий
#   -y, --yes                     — доставить недостающие пакеты без вопроса
#
# Ставится симлинками, а не копиями: правка файла в репозитории действует
# сразу, без переустановки, а `git pull` обновляет все поставленные скиллы разом.
#
# Что куда идёт (соглашение, новые скиллы ничего здесь не меняют):
#
#   skills/<категория>/<имя>/SKILL.md   → каталог линкуется в <область>/skills/<имя>
#   skills/<категория>/<имя>/bin/*      → команды, по одной в ~/.local/bin/
#   skills/<категория>/<имя>/claude/*   → файлы окружения Claude, в ~/.claude/
#   skills/<категория>/<имя>/permissions.json, hooks.json → в ~/.claude/settings.json
#   skills/<категория>/<имя>/requires.txt → команды, которых не хватает в системе
#
# Область — либо ~/.claude (глобально), либо <проект>/.claude (--local). Команды и файлы
# окружения ставятся глобально в любом случае: $PATH один на систему, и скрипты скиллов
# ищут своё окружение по путям от $HOME.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$REPO/skills"

HOME_CLAUDE="${CLAUDE_HOME:-$HOME/.claude}"
DEST_BIN="$HOME/.local/bin"
AGENTS_SKILLS="$HOME/.agents/skills"
# Бэкапы лежат в стороне: каталог с бэкапом внутри skills/ Claude Code
# принял бы за ещё один скилл, и рядом с afk появился бы двойник afk.backup-…
BACKUP_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/claude-skills/backups"

DRY=0
FORCE=0
ASSUME_YES=0
UNINSTALL=0
LIST=0
PICK=0
AGENTS=0
PROJECT=""
WANTED=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) DRY=1; shift ;;
    --yes|-y)     ASSUME_YES=1; shift ;;
    --force|-f)   FORCE=1; shift ;;
    --uninstall)  UNINSTALL=1; shift ;;
    --list|-l)    LIST=1; shift ;;
    -i|--pick|--interactive) PICK=1; shift ;;
    --agents)     AGENTS=1; shift ;;
    --local)      PROJECT="$PWD"; shift ;;
    --project)
      [[ -n "${2:-}" ]] || { echo "install: --project ждёт путь" >&2; exit 2; }
      PROJECT="$2"; shift 2 ;;
    -h|--help)    sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)           echo "install: неизвестный флаг $1" >&2; exit 2 ;;
    *)            WANTED+=("$1"); shift ;;
  esac
done

# ── область установки ──
if [[ -n "$PROJECT" ]]; then
  PROJECT="$(cd "$PROJECT" 2>/dev/null && pwd)" || {
    echo "install: нет такого каталога — $PROJECT" >&2; exit 1; }
  if [[ "$PROJECT" == "$HOME" ]]; then
    echo "install: проект — это домашний каталог; для всех проектов ставь без --local" >&2
    exit 2
  fi
  SKILL_DESTS=("$PROJECT/.claude/skills")
  SCOPE="проект $(basename "$PROJECT")"
else
  SKILL_DESTS=("$HOME_CLAUDE/skills")
  SCOPE="глобально"
fi

if (( AGENTS )); then
  if [[ -n "$PROJECT" ]]; then
    echo "install: --agents не сочетается с --local — каталог ~/.agents/skills общий" >&2
    exit 2
  fi
  SKILL_DESTS+=("$AGENTS_SKILLS")
  SCOPE="$SCOPE + ~/.agents/skills"
fi
DEST_CLAUDE="$HOME_CLAUDE"

# ── вывод ──
c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
[[ -t 1 ]] || { c_ok=""; c_warn=""; c_dim=""; c_off=""; }

linked=0; skipped=0; conflicts=0; removed=0
RUN_STAMP="$(date +%Y%m%d-%H%M%S)"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$c_ok" "$c_off" "$*"; }
warn() { printf '  %s!%s %s\n' "$c_warn" "$c_off" "$*"; }
dim()  { printf '  %s·%s %s\n' "$c_dim" "$c_off" "$*"; }

# Путь для показа: домашний — через ~, проектный — от корня проекта.
short() {
  local p="$1"
  [[ -n "$PROJECT" && "$p" == "$PROJECT"/* ]] && { printf '%s' "${p#$PROJECT/}"; return; }
  printf '%s' "${p/#$HOME/\~}"
}

# Все скиллы репозитория как «категория/имя»; deprecated не ставим.
all_skills() {
  local f d
  while IFS= read -r -d '' f; do
    d="$(dirname "$f")"
    printf '%s\n' "${d#"$SKILLS_DIR"/}"
  done < <(find "$SKILLS_DIR" -name SKILL.md -not -path '*/deprecated/*' -print0 2>/dev/null) | sort
}

describe() {
  sed -n 's/^description:[[:space:]]*//p' "$SKILLS_DIR/$1/SKILL.md" 2>/dev/null \
    | head -1 | cut -c1-88
}

# Скилл называют коротко («afk») или полно («autonomy/afk») — понимаем оба.
resolve_skill() {
  local want="$1" s
  for s in "${AVAILABLE[@]}"; do
    [[ "$s" == "$want" || "${s##*/}" == "$want" ]] && { printf '%s' "$s"; return 0; }
  done
  return 1
}

# ── одна связь: link <источник в репо> <место назначения> ──
link() {
  local src="$1" dst="$2" rel; rel="$(short "$2")"

  if [[ -L "$dst" ]]; then
    local cur; cur="$(readlink -f "$dst" 2>/dev/null || true)"
    if [[ -n "$cur" && "$cur" == "$(readlink -f "$src")" ]]; then
      dim "$rel — уже стоит"; skipped=$((skipped + 1)); return
    fi
    # Битый или переехавший симлинк в этот же репозиторий чиним молча.
    local raw; raw="$(readlink "$dst")"
    if [[ "$cur" == "$REPO"/* || "$raw" == "$REPO"/* ]]; then
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
      local bak="$BACKUP_ROOT/$RUN_STAMP/${dst#/}"
      (( DRY )) || { mkdir -p "$(dirname "$bak")"; mv "$dst" "$bak"; ln -s "$src" "$dst"; }
      ok "$rel — заменён, старое в $(short "$bak")"; linked=$((linked + 1)); return
    fi
    warn "$rel — существует и это не симлинк, пропускаю; --force заменит с бэкапом"
    conflicts=$((conflicts + 1)); return
  fi

  (( DRY )) || { mkdir -p "$(dirname "$dst")"; ln -s "$src" "$dst"; }
  ok "$rel"; linked=$((linked + 1))
}

# ── снятие: удаляем только то, что ведёт в этот репозиторий ──
unlink_if_ours() {
  local dst="$1" rel; rel="$(short "$1")"
  [[ -L "$dst" ]] || return 0
  local cur raw; cur="$(readlink -f "$dst" 2>/dev/null || true)"; raw="$(readlink "$dst")"
  [[ "$cur" == "$REPO"/* || "$raw" == "$REPO"/* ]] || return 0
  (( DRY )) || rm "$dst"
  ok "снят $rel"; removed=$((removed + 1))
}

# ── разрешения скилла ──
# Правила из permissions.json уезжают в ~/.claude/settings.json: команды скилла
# должны проходить без подтверждения, иначе автономный режим упрётся в вопрос
# ровно тогда, когда спрашивать уже некого.
settings_rules() {
  local dir="$1" mode="$2" settings="$HOME/.claude/settings.json" kind file changed
  for kind in permissions hooks; do
    file="$dir/$kind.json"
    [[ -f "$file" ]] || continue
    if (( DRY )); then
      ok "$(short "$settings") — $kind скилла"; continue
    fi
    local own=()
    if [[ -d "$dir/bin" ]]; then
      for f in "$dir"/bin/*; do [[ -f "$f" ]] && own+=("$(basename "$f")"); done
    fi
    changed=$(python3 "$REPO/scripts/settings-merge.py" "$kind" "$file" "$settings" "$mode" "${own[@]}")
    [[ -n "$changed" ]] && ok "$(short "$settings") — $kind скилла"
  done
  return 0
}

# ── чего скиллу не хватает в системе ──
# requires.txt перечисляет команды, без которых скилл работает не полностью.
# Установщик их проверяет и предлагает доставить пакетным менеджером — молча
# пропускать нельзя: недостающий tmux, например, стоит скиллу целой ночи.
INSTALL_CMD=""
detect_pm() {
  [[ -n "$INSTALL_CMD" ]] && return 0
  if   command -v pacman  >/dev/null; then INSTALL_CMD="sudo pacman -S --needed"
  elif command -v apt-get >/dev/null; then INSTALL_CMD="sudo apt-get install -y"
  elif command -v dnf     >/dev/null; then INSTALL_CMD="sudo dnf install -y"
  elif command -v zypper  >/dev/null; then INSTALL_CMD="sudo zypper install -y"
  elif command -v apk     >/dev/null; then INSTALL_CMD="sudo apk add"
  elif command -v brew    >/dev/null; then INSTALL_CMD="brew install"
  fi
}

# Вопрос человеку, когда установщик запущен руками.
ask() {
  local answer
  printf '%s [y/N] ' "$1"
  read -r answer || answer=""
  [[ "$answer" =~ ^[YyДд] ]]
}

check_requires() {
  local dir="$1" file="$1/requires.txt" line cmd why missing=()
  [[ -f "$file" ]] || return 0

  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# || -z "${line// }" ]] && continue
    cmd="${line%%[[:space:]]*}"
    why="${line#*— }"
    command -v "$cmd" >/dev/null && continue
    missing+=("$cmd")
    warn "нет $cmd — $why"
  done < "$file"

  (( ${#missing[@]} )) || return 0
  detect_pm
  if [[ -z "$INSTALL_CMD" ]]; then
    say "  поставь сам: ${missing[*]}"
    return 0
  fi
  if (( DRY )); then
    say "  поставил бы: $INSTALL_CMD ${missing[*]}"
    return 0
  fi
  if (( ASSUME_YES )) || { [[ -t 0 ]] && ask "  поставить сейчас ($INSTALL_CMD ${missing[*]})?"; }; then
    # shellcheck disable=SC2086
    $INSTALL_CMD "${missing[@]}" && ok "доставлено: ${missing[*]}"
  else
    say "  без них скилл работает не полностью: $INSTALL_CMD ${missing[*]}"
  fi
  return 0
}

# Разовая настройка скилла: у кого есть setup.sh, тот сам спросит нужное
# и запишет ответ в ~/.config/claude-skills/. Установщику знать, о чём именно
# спрашивают, не требуется.
run_setup() {
  local file="$1/setup.sh"
  [[ -f "$file" ]] || return 0
  if (( DRY )); then
    dim "выполнил бы настройку: setup.sh"
    return 0
  fi
  SKILLS_ASSUME_YES="$ASSUME_YES" bash "$file" || warn "настройка скилла не прошла"
}

install_skill() {
  local rel="$1" dir="$SKILLS_DIR/$1" name="${1##*/}" f d
  say "$rel"

  for d in "${SKILL_DESTS[@]}"; do link "$dir" "$d/$name"; done

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

  settings_rules "$dir" add
  check_requires "$dir"
  run_setup "$dir"
}

uninstall_skill() {
  local rel="$1" dir="$SKILLS_DIR/$1" name="${1##*/}" f d
  say "$rel"

  for d in "${SKILL_DESTS[@]}"; do unlink_if_ours "$d/$name"; done

  # Из проекта снимаем только сам скилл: команды и файлы окружения стоят глобально
  # и нужны другим проектам — снять их отсюда значило бы сломать соседей.
  if [[ -n "$PROJECT" ]]; then
    [[ -d "$dir/bin" || -d "$dir/claude" ]] && dim "команды и окружение оставлены — они общие"
    return 0
  fi

  if [[ -d "$dir/bin" ]]; then
    for f in "$dir"/bin/*; do
      [[ -f "$f" ]] && unlink_if_ours "$DEST_BIN/$(basename "$f")"
    done
  fi
  if [[ -d "$dir/claude" ]]; then
    for f in "$dir"/claude/*; do
      [[ -e "$f" ]] && unlink_if_ours "$DEST_CLAUDE/$(basename "$f")"
    done
  fi

  settings_rules "$dir" remove
  return 0
}

# ── выбор руками ──
pick_skills() {
  local i=1 s answer chosen=()

  printf 'Скиллы репозитория:\n\n' >&2
  for s in "${AVAILABLE[@]}"; do
    printf '  %2d) %-22s %s\n' "$i" "$s" "$(describe "$s")" >&2
    i=$((i + 1))
  done
  printf '\nНомера через пробел (пусто — все, q — выход): ' >&2
  # Терминал, если он есть; иначе stdin — чтобы выбор можно было и передать по конвейеру.
  if { exec 3</dev/tty; } 2>/dev/null; then
    read -r answer <&3 || answer=""
    exec 3<&-
  else
    read -r answer || answer=""
  fi

  # 97 — «человек отменил»; функция работает в подоболочке, обычный выход отсюда не выйдет.
  [[ "$answer" == "q" || "$answer" == "й" ]] && exit 97
  if [[ -z "${answer// }" ]]; then
    printf '%s\n' "${AVAILABLE[@]}"; return
  fi

  for n in $answer; do
    [[ "$n" =~ ^[0-9]+$ ]] || { echo "install: «$n» — не номер" >&2; exit 2; }
    (( n >= 1 && n <= ${#AVAILABLE[@]} )) || { echo "install: нет пункта $n" >&2; exit 2; }
    chosen+=("${AVAILABLE[$((n - 1))]}")
  done
  printf '%s\n' "${chosen[@]}"
}

# ── что ставим ──
mapfile -t AVAILABLE < <(all_skills)
if [[ ${#AVAILABLE[@]} -eq 0 ]]; then
  echo "install: в $SKILLS_DIR нет ни одного скилла" >&2
  exit 1
fi

if (( LIST )); then
  for s in "${AVAILABLE[@]}"; do printf '%-24s %s\n' "$s" "$(describe "$s")"; done
  exit 0
fi

TARGETS=()
if [[ ${#WANTED[@]} -gt 0 ]]; then
  for w in "${WANTED[@]}"; do
    resolved="$(resolve_skill "$w")" || {
      echo "install: нет такого скилла — $w (см. ./install.sh --list)" >&2; exit 1; }
    TARGETS+=("$resolved")
  done
elif (( PICK )); then
  # Через переменную, а не подстановку процесса: иначе ошибка выбора потеряется
  # вместе с кодом возврата, и установка молча пройдёт вхолостую.
  picked="$(pick_skills)" || { rc=$?; (( rc == 97 )) && exit 0; exit "$rc"; }
  [[ -n "$picked" ]] || { echo "install: ничего не выбрано" >&2; exit 1; }
  mapfile -t TARGETS <<< "$picked"
else
  TARGETS=("${AVAILABLE[@]}")
fi

say "${c_dim}область: $SCOPE${c_off}"
(( DRY )) && say "${c_dim}пробный прогон, ничего не меняется${c_off}"
say ""

if (( UNINSTALL )); then
  for s in "${TARGETS[@]}"; do uninstall_skill "$s"; say ""; done
  say "снято: $removed"
  exit 0
fi

if (( ! DRY )); then
  mkdir -p "${SKILL_DESTS[@]}" "$DEST_BIN"
fi

for s in "${TARGETS[@]}"; do install_skill "$s"; say ""; done

summary="поставлено: $linked, уже было: $skipped"
(( conflicts > 0 )) && summary="$summary, пропущено из-за конфликта: $conflicts"
say "$summary"

# ── послесловие ──
if [[ -n "$PROJECT" ]]; then
  say ""
  say "Скиллы видны только в этом проекте; команды и файлы окружения — общие."
  grep -qs '^\.claude/$\|^\.claude$' "$PROJECT/.gitignore" \
    || say "Симлинки лежат в .claude/skills — реши, коммитить их или добавить в .gitignore."
fi

case ":$PATH:" in
  *":$DEST_BIN:"*) ;;
  *) say ""
     warn "$(short "$DEST_BIN") не в \$PATH — команды скиллов не запустятся"
     say "     добавь в ~/.zshrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

for s in "${TARGETS[@]}"; do
  if [[ -f "$SKILLS_DIR/$s/SETUP.md" ]]; then
    say ""
    say "${s##*/} требует настройки — см. skills/$s/SETUP.md"
  fi
done

(( conflicts > 0 )) && exit 3
exit 0
