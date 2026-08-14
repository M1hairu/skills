#!/usr/bin/env bash
# Проверка репозитория: фронтматтер скиллов, скрипты, инварианты, следы личных данных.
#
#   ./scripts/validate.sh          — проверить всё
#   ./scripts/validate.sh afk      — проверить один скилл
#
# Ошибка (✗) валит проверку, замечание (!) — нет.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$REPO/skills"
PLUGIN="$REPO/.claude-plugin/plugin.json"

c_err=$'\033[31m'; c_warn=$'\033[33m'; c_ok=$'\033[32m'; c_off=$'\033[0m'
[[ -t 1 ]] || { c_err=""; c_warn=""; c_ok=""; c_off=""; }

errors=0; warnings=0

err()  { printf '  %s✗%s %s\n' "$c_err" "$c_off" "$*"; errors=$((errors + 1)); }
warn() { printf '  %s!%s %s\n' "$c_warn" "$c_off" "$*"; warnings=$((warnings + 1)); }

# Личное, чему в публичном репозитории не место.
scan_secrets() {
  local file="$1" rel="${1#"$REPO"/}" hits

  hits=$(grep -nE '(-----BEGIN [A-Z ]*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{30,}|sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|[0-9]{8,10}:[A-Za-z0-9_-]{35})' "$file" 2>/dev/null | head -3)
  [[ -n "$hits" ]] && err "$rel: похоже на ключ или токен — $(head -1 <<< "$hits" | cut -c1-70)"

  # Публичные IP: приватные диапазоны и localhost не в счёт.
  hits=$(grep -noE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' "$file" 2>/dev/null \
         | grep -vE ':(127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|255\.|1\.2\.3\.4|0\.1)' | head -3)
  [[ -n "$hits" ]] && err "$rel: адрес сервера в тексте (строка ${hits%%:*}) — вынеси в конфиг"

  hits=$(grep -nE '\b[a-z_][a-z0-9_-]*@([a-z0-9-]+\.)+[a-z]{2,}\b' "$file" 2>/dev/null \
         | grep -viE 'example\.(com|org)|@users\.noreply' | head -2)
  [[ -n "$hits" ]] && warn "$rel: адрес или логин в тексте — $(head -1 <<< "$hits" | cut -c1-60)"

  hits=$(grep -nE '/home/[a-z][a-z0-9_-]*(/|$)' "$file" 2>/dev/null | head -2)
  [[ -n "$hits" ]] && warn "$rel: путь с именем пользователя (строка ${hits%%:*}) — используй \$HOME"
}

# rel — «категория/имя»
check_skill() {
  local rel="$1" dir="$SKILLS_DIR/$1" name="${1##*/}" category="${1%%/*}"
  printf '%s\n' "$rel"

  local skill="$dir/SKILL.md"
  if [[ ! -f "$skill" ]]; then
    err "нет SKILL.md"; return
  fi

  # ── фронтматтер ──
  if [[ "$(head -1 "$skill")" != "---" ]]; then
    err "SKILL.md не начинается с фронтматтера ---"
  else
    local fm_end
    fm_end=$(awk 'NR>1 && /^---[[:space:]]*$/ {print NR; exit}' "$skill")
    if [[ -z "$fm_end" ]]; then
      err "фронтматтер не закрыт"
    else
      local fm fm_name fm_desc
      fm=$(sed -n "2,$((fm_end - 1))p" "$skill")
      fm_name=$(sed -n 's/^name:[[:space:]]*//p' <<< "$fm" | head -1 | tr -d '"'"'"'')
      fm_desc=$(sed -n 's/^description:[[:space:]]*//p' <<< "$fm" | head -1 | tr -d '"'"'"'')

      if [[ -z "$fm_name" ]]; then
        err "во фронтматтере нет name"
      else
        [[ "$fm_name" == "$name" ]] || err "name «$fm_name» не совпадает с каталогом «$name»"
        [[ "$fm_name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || err "name «$fm_name»: только строчные буквы, цифры и дефисы"
        (( ${#fm_name} <= 64 )) || err "name длиннее 64 символов"
      fi

      if [[ -z "$fm_desc" ]]; then
        err "во фронтматтере нет description — без него скилл не подхватится"
      else
        local dlen=${#fm_desc}
        (( dlen <= 1024 )) || err "description $dlen символов, предел 1024"
        (( dlen >= 40 ))   || warn "description короткий ($dlen символов): по нему решается, звать ли скилл"
      fi
    fi
  fi

  # ── объём ──
  local lines; lines=$(wc -l < "$skill")
  (( lines <= 500 )) || warn "SKILL.md $lines строк — больше 500, пора выносить детали в отдельные файлы"

  # ── скрипты ──
  local f
  if [[ -d "$dir/bin" ]]; then
    for f in "$dir"/bin/*; do
      [[ -f "$f" ]] || continue
      local b; b=$(basename "$f")
      [[ -x "$f" ]] || err "bin/$b не исполняемый (chmod +x)"
      head -1 "$f" | grep -q '^#!' || err "bin/$b без шебанга"
      if head -1 "$f" | grep -q 'bash'; then
        bash -n "$f" 2>/dev/null || err "bin/$b: синтаксическая ошибка"
      fi
    done
  fi

  # ── инварианты: где скилл обязан быть упомянут ──
  local entry="./skills/$rel"
  if [[ "$category" == "deprecated" ]]; then
    grep -qF "\"$entry\"" "$PLUGIN" 2>/dev/null && err "выведенный скилл остался в plugin.json"
    [[ -f "$REPO/docs/$rel.md" ]] && err "выведенный скилл сохранил страницу docs/$rel.md"
    grep -qF "skills/$rel/SKILL.md" "$REPO/README.md" 2>/dev/null && err "выведенный скилл остался в README.md"
  else
    grep -qF "\"$entry\"" "$PLUGIN" 2>/dev/null \
      || err "нет записи в .claude-plugin/plugin.json — поставившие плагином скилл не получат"
    [[ -f "$REPO/docs/$rel.md" ]] \
      || err "нет страницы docs/$rel.md (см. .agents/writing-docs.md)"
    grep -qF "skills/$rel/SKILL.md" "$REPO/README.md" 2>/dev/null \
      || err "не упомянут в README.md"
    grep -qsF "$name/SKILL.md" "$SKILLS_DIR/$category/README.md" \
      || err "не упомянут в skills/$category/README.md"
  fi

  # ── свои проверки скилла ──
  # Скилл, у которого есть что проверить (правила хука, разбор вывода), кладёт
  # это в tests/run.sh. Молчаливая поломка таких правил дороже всего.
  if [[ -x "$dir/tests/run.sh" ]]; then
    local out
    if out=$("$dir/tests/run.sh" 2>&1); then
      printf '  %s✓%s tests/run.sh —%s\n' "$c_ok" "$c_off" "$(tail -1 <<< "$out")"
    else
      err "tests/run.sh не прошёл:"
      printf '%s\n' "$out" | sed 's/^/    /'
    fi
  fi

  # ── личные данные ──
  while IFS= read -r f; do scan_secrets "$f"; done < <(find "$dir" -type f \
      \( -name '*.md' -o -name '*.sh' -o -path '*/bin/*' \) ! -name '*.example*')

  printf '\n'
}

# ── общее по репозиторию ──
check_repo() {
  printf 'репозиторий\n'

  [[ -f "$REPO/install.sh" && -x "$REPO/install.sh" ]] || err "install.sh отсутствует или не исполняемый"

  local j
  for j in "$PLUGIN" "$REPO/.claude-plugin/marketplace.json"; do
    if [[ -f "$j" ]]; then
      python3 -m json.tool "$j" >/dev/null 2>&1 || err "${j#"$REPO"/}: битый JSON"
    else
      err "нет ${j#"$REPO"/}"
    fi
  done

  # Обратная сторона инварианта: в plugin.json нет записей без скилла.
  if [[ -f "$PLUGIN" ]]; then
    local entry
    while IFS= read -r entry; do
      [[ -f "$REPO/${entry#./}/SKILL.md" ]] \
        || err "plugin.json ссылается на несуществующий скилл: $entry"
    done < <(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1], encoding="utf-8"))["skills"]))' "$PLUGIN" 2>/dev/null)
  fi

  # Категория без README — скиллы в ней негде перечислить.
  local d
  for d in "$SKILLS_DIR"/*/; do
    [[ -d "$d" ]] || continue
    [[ "$(basename "$d")" == "deprecated" ]] && continue
    [[ -f "$d/README.md" ]] || err "нет skills/$(basename "$d")/README.md"
  done

  local f
  for f in "$REPO"/*.md "$REPO"/scripts/*.sh "$REPO"/install.sh; do
    [[ -f "$f" ]] && scan_secrets "$f"
  done

  printf '\n'
}

mapfile -t ALL < <(
  find "$SKILLS_DIR" -name SKILL.md -print0 2>/dev/null \
    | while IFS= read -r -d '' f; do d="$(dirname "$f")"; printf '%s\n' "${d#"$SKILLS_DIR"/}"; done | sort
)

TARGETS=()
if [[ $# -gt 0 ]]; then
  for w in "$@"; do
    for s in "${ALL[@]}"; do
      [[ "$s" == "$w" || "${s##*/}" == "$w" ]] && TARGETS+=("$s")
    done
  done
  [[ ${#TARGETS[@]} -gt 0 ]] || { echo "validate: нет такого скилла — $*" >&2; exit 1; }
else
  TARGETS=("${ALL[@]}")
  check_repo
fi

for s in "${TARGETS[@]}"; do check_skill "$s"; done

if (( errors )); then
  printf '%sошибок: %d%s, замечаний: %d\n' "$c_err" "$errors" "$c_off" "$warnings"
  exit 1
fi
printf '%s✓ скиллов проверено: %d%s, замечаний: %d\n' "$c_ok" "${#TARGETS[@]}" "$c_off" "$warnings"
