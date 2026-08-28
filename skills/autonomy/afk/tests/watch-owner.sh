#!/usr/bin/env bash
# Проверка того, как сторож понимает, жива ли охраняемая работа.
#
#   ./tests/watch-owner.sh    — прогнать
#
# Список сессий подсовывается фальшивый (`claude agents --json` подменён в PATH),
# поэтому проверка не зависит от того, что запущено на машине. Сторож гоняется
# в режиме `--once`: он рассуждает вслух, но ничего не поднимает.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCH="$HERE/../bin/afk-watch"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAKE="$TMP/bin"
mkdir -p "$FAKE"
cat > "$FAKE/claude" <<'FAKESH'
#!/usr/bin/env bash
[[ "${1:-}" == agents ]] && { cat "$FAKE_AGENTS"; exit 0; }
exit 0
FAKESH
chmod +x "$FAKE/claude"

PROJ="$TMP/proj"; mkdir -p "$PROJ/.afk"
OTHER="$TMP/home"; mkdir -p "$OTHER"

fail=0
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  ПЛОХО %s\n     ждали: %s\n     вышло: %s\n' "$1" "$2" "$3"; fail=1; }

# agents_json <cwd|sid|pid|status>...  — фальшивый список сессий
agents_json() {
  local out="[" first=1 row cwd sid pid status
  for row in "$@"; do
    IFS='|' read -r cwd sid pid status <<< "$row"
    [[ $first == 1 ]] || out+=","
    first=0
    out+="{\"cwd\":\"$cwd\",\"sessionId\":\"$sid\",\"pid\":$pid,\"status\":\"$status\","
    out+="\"kind\":\"interactive\",\"startedAt\":1000,\"name\":\"проба\"}"
  done
  out+="]"
  printf '%s\n' "$out" > "$TMP/agents.json"
}

state() { printf '%s\n' "$@" > "$PROJ/.afk/watch.state"; }

# Дом тоже подставной: так проверка не читает настройки машины и не ищет
# журналы сессий в настоящем ~/.claude.
HOME_FAKE="$TMP/fakehome"; mkdir -p "$HOME_FAKE"

run() {
  PATH="$FAKE:$PATH" FAKE_AGENTS="$TMP/agents.json" AFK_PING_URL="file:///etc/hostname" \
    HOME="$HOME_FAKE" "$WATCH" --once --dir "$PROJ" 2>&1 || true
}

# Журнал сессии с обрывом связи: сторож ищет в нём пометку isApiErrorMessage.
broke_journal() {
  local sid="$1" ago="$2" dir="$HOME_FAKE/.claude/projects/-проба"
  mkdir -p "$dir"
  python3 - "$dir/$sid.jsonl" "$ago" <<'PYEOF'
import json, sys, time
from datetime import datetime, timezone
stamp = datetime.fromtimestamp(time.time() - int(sys.argv[2]), timezone.utc).isoformat().replace("+00:00", "Z")
with open(sys.argv[1], "w") as f:
    f.write(json.dumps({"type": "assistant", "timestamp": stamp, "isApiErrorMessage": True}) + "\n")
PYEOF
}

now() { date +%s; }

# 1. Хозяйка жива в другом каталоге — работа на месте, поднимать нечего.
agents_json "$OTHER|aaaa1111|4242|idle"
state "started=$(( $(now) - 3600 ))" "owner_pid=4242" "owner_sid=aaaa1111"
touch -d "@$(( $(now) - 4000 ))" "$PROJ/.afk/heartbeat"
out="$(run)"
if [[ "$out" == *"ночного пульса нет"* && "$out" != *"поднял бы"* ]]; then
  ok "сессия-хозяйка из другого каталога считается живой работой"
else
  bad "сессия-хозяйка из другого каталога считается живой работой" "«ночного пульса нет», без подъёма" "$out"
fi

# 2. Хозяйка исчезла, пауза после старта прошла — работу поднимают.
agents_json
state "started=$(( $(now) - 3600 ))" "owner_pid=4242" "owner_sid=aaaa1111"
out="$(run)"
if [[ "$out" == *"сессии в каталоге не осталось"* && "$out" == *"поднял бы"* ]]; then
  ok "исчезнувшую работу сторож поднимает"
else
  bad "исчезнувшую работу сторож поднимает" "«сессии в каталоге не осталось» и подъём" "$out"
fi

# 3. Сессии ещё нет, но сторож только заступил — это не пропажа, а `afk`,
#    который заводит Claude Code следующей командой.
agents_json
state "started=$(now)" "owner_pid=" "owner_sid="
out="$(run)"
if [[ "$out" == *"сессии пока нет"* && "$out" != *"поднял бы"* ]]; then
  ok "в первые секунды после старта дубль не заводится"
else
  bad "в первые секунды после старта дубль не заводится" "«сессии пока нет», без подъёма" "$out"
fi

# 4. Хозяйка жива, пульс был, но давно замолк: чужую сессию сторож не трогает.
agents_json "$OTHER|aaaa1111|4242|idle"
state "started=$(( $(now) - 7200 ))" "owner_pid=4242" "owner_sid=aaaa1111"
touch -d "@$(( $(now) - 3600 ))" "$PROJ/.afk/heartbeat"
out="$(run)"
if [[ "$out" == *"работу ведёт сессия человека"* && "$out" != *"поднял бы"* ]]; then
  ok "молчание чужой сессии не повод её закрывать"
else
  bad "молчание чужой сессии не повод её закрывать" "«работу ведёт сессия человека», без подъёма" "$out"
fi

# 5. Сессия в самом каталоге — прежнее поведение не изменилось.
agents_json "$PROJ|bbbb2222|4343|busy"
state "started=$(( $(now) - 7200 ))" "owner_pid=" "owner_sid="
touch -d "@$(( $(now) - 3600 ))" "$PROJ/.afk/heartbeat"
out="$(run)"
if [[ "$out" == *"занята работой"* && "$out" != *"поднял бы"* ]]; then
  ok "занятую сессию в каталоге сторож по-прежнему не трогает"
else
  bad "занятую сессию в каталоге сторож по-прежнему не трогает" "«сессия занята работой»" "$out"
fi

# 6. Хозяйка оборвалась — вот это повод закрыть её и поднять работу.
agents_json "$OTHER|aaaa1111|4242|idle"
state "started=$(( $(now) - 7200 ))" "owner_pid=4242" "owner_sid=aaaa1111"
touch -d "@$(( $(now) - 3600 ))" "$PROJ/.afk/heartbeat"
broke_journal aaaa1111 1800
out="$(run)"
if [[ "$out" == *"оборвалась"* && "$out" == *"закрыл вставшую сессию aaaa1111"* && "$out" == *"контекст aaaa1111"* ]]; then
  ok "оборванную хозяйку сторож закрывает и поднимает с её контекстом"
else
  bad "оборванную хозяйку сторож закрывает и поднимает с её контекстом" "обрыв, закрытие aaaa1111, подъём с её контекстом" "$out"
fi
rm -rf "$HOME_FAKE/.claude"

# 7. Рядом с хозяйкой есть сессия самого каталога — поднимать надо с её
#    контекстом: журнал хозяйки лежит в другом проекте и здесь не откроется.
agents_json "$OTHER|aaaa1111|4242|idle" "$PROJ|bbbb2222|4343|idle"
state "started=$(( $(now) - 7200 ))" "owner_pid=4242" "owner_sid=aaaa1111"
touch -d "@$(( $(now) - 3600 ))" "$PROJ/.afk/heartbeat"
out="$(run)"
if [[ "$out" == *"контекст bbbb2222"* ]]; then
  ok "контекст для подъёма берётся из сессии каталога, а не у хозяйки"
else
  bad "контекст для подъёма берётся из сессии каталога, а не у хозяйки" "«контекст bbbb2222»" "$out"
fi

(( fail == 0 )) && { echo "сторож: все проверки пройдены"; exit 0; }
echo "сторож: есть непройденные проверки" >&2
exit 1
