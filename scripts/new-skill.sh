#!/usr/bin/env bash
# Завести новый скилл из шаблона.
#
#   ./scripts/new-skill.sh имя-скилла
#   ./scripts/new-skill.sh имя-скилла --bin        — сразу с командой skills/имя/bin/имя
#   ./scripts/new-skill.sh имя-скилла --claude     — сразу с файлом окружения skills/имя/claude/
#
# Дальше правишь SKILL.md и ставишь: ./install.sh имя-скилла

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

NAME="${1:-}"
shift || true

WITH_BIN=0
WITH_CLAUDE=0
for a in "$@"; do
  case "$a" in
    --bin)    WITH_BIN=1 ;;
    --claude) WITH_CLAUDE=1 ;;
    *) echo "new-skill: неизвестный флаг $a" >&2; exit 2 ;;
  esac
done

if [[ -z "$NAME" ]]; then
  echo "использование: ./scripts/new-skill.sh имя-скилла [--bin] [--claude]" >&2
  exit 2
fi

if [[ ! "$NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "new-skill: имя — только строчные буквы, цифры и дефисы (получено «$NAME»)" >&2
  exit 2
fi

DIR="$REPO/skills/$NAME"
[[ -e "$DIR" ]] && { echo "new-skill: skills/$NAME уже есть" >&2; exit 1; }

mkdir -p "$DIR"
sed "s/{{NAME}}/$NAME/g" "$REPO/template/SKILL.md" > "$DIR/SKILL.md"
echo "создан skills/$NAME/SKILL.md"

if (( WITH_BIN )); then
  mkdir -p "$DIR/bin"
  cat > "$DIR/bin/$NAME" <<EOF
#!/usr/bin/env bash
# Что делает команда — одной строкой.
#
#   $NAME            — …
#
set -euo pipefail

echo "$NAME: ещё не написано"
EOF
  chmod +x "$DIR/bin/$NAME"
  echo "создан skills/$NAME/bin/$NAME → попадёт в ~/.local/bin/$NAME"
fi

if (( WITH_CLAUDE )); then
  mkdir -p "$DIR/claude"
  echo "создан skills/$NAME/claude/ → его содержимое попадёт в ~/.claude/"
fi

cat <<EOF

дальше:
  1. напиши SKILL.md — важнее всего description, по нему скилл находят
  2. ./scripts/validate.sh $NAME
  3. ./install.sh $NAME
EOF
