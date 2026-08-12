#!/usr/bin/env bash
# Завести новый скилл из шаблона.
#
#   ./scripts/new-skill.sh autonomy/имя-скилла
#   ./scripts/new-skill.sh autonomy/имя --bin      — сразу с командой bin/имя
#   ./scripts/new-skill.sh autonomy/имя --claude   — сразу с файлом окружения claude/
#
# Заводит каталог скилла, заготовку страницы в docs/ и запись в plugin.json.
# Дальше правишь SKILL.md, дописываешь оба README.md и ставишь: ./install.sh имя

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TARGET="${1:-}"
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

if [[ -z "$TARGET" ]]; then
  echo "использование: ./scripts/new-skill.sh <категория>/<имя> [--bin] [--claude]" >&2
  echo "категории:" >&2
  find "$REPO/skills" -mindepth 1 -maxdepth 1 -type d -printf '  %f\n' >&2
  exit 2
fi

if [[ "$TARGET" != */* ]]; then
  echo "new-skill: нужна категория — например autonomy/$TARGET" >&2
  exit 2
fi

CATEGORY="${TARGET%%/*}"
NAME="${TARGET##*/}"

if [[ ! "$NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "new-skill: имя — только строчные буквы, цифры и дефисы (получено «$NAME»)" >&2
  exit 2
fi

DIR="$REPO/skills/$CATEGORY/$NAME"
[[ -e "$DIR" ]] && { echo "new-skill: skills/$CATEGORY/$NAME уже есть" >&2; exit 1; }

mkdir -p "$DIR"
sed "s/{{NAME}}/$NAME/g" "$REPO/template/SKILL.md" > "$DIR/SKILL.md"
echo "создан skills/$CATEGORY/$NAME/SKILL.md"

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
  echo "создан skills/$CATEGORY/$NAME/bin/$NAME → попадёт в ~/.local/bin/$NAME"
fi

if (( WITH_CLAUDE )); then
  mkdir -p "$DIR/claude"
  echo "создан skills/$CATEGORY/$NAME/claude/ → его содержимое попадёт в ~/.claude/"
fi

# ── страница в docs/ ──
if [[ "$CATEGORY" != "deprecated" ]]; then
  mkdir -p "$REPO/docs/$CATEGORY"
  cat > "$REPO/docs/$CATEGORY/$NAME.md" <<EOF
## Что делает

Работа скилла одной фразой, затем определяющее ограничение — тот единственный факт, из-за
которого он ведёт себя не так, как повёл бы себя агент без него.

## Когда за ним тянуться

Кто его запускает и в какой ситуации он уместен. Здесь же граница с соседними скиллами.

## Частые вопросы

**Настоящий вопрос, который уже задавали?**
Ответ.

## Работает, если

Как выглядит успех со стороны человека — и характерный провал.
EOF
  echo "создан docs/$CATEGORY/$NAME.md — заполнить по .agents/writing-docs.md"

  # ── запись в plugin.json ──
  python3 - "$REPO/.claude-plugin/plugin.json" "./skills/$CATEGORY/$NAME" <<'PY'
import json, sys

path, entry = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    data = json.load(f)

if entry not in data["skills"]:
    data["skills"].append(entry)
    data["skills"].sort()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"добавлено в plugin.json: {entry}")
PY
fi

cat <<EOF

дальше:
  1. напиши SKILL.md — важнее всего description, по нему скилл находят
  2. допиши ссылку в README.md и в skills/$CATEGORY/README.md
  3. ./scripts/validate.sh
  4. ./install.sh $NAME
EOF
