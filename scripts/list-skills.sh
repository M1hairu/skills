#!/usr/bin/env bash
# Перечислить скиллы репозитория.
#
#   ./scripts/list-skills.sh          — категория/имя и описание
#   ./scripts/list-skills.sh --paths  — только пути до SKILL.md

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${1:-}" == "--paths" ]]; then
  cd "$REPO"
  find skills -name SKILL.md | sort
  exit 0
fi

cd "$REPO"
while IFS= read -r skill_md; do
  rel="${skill_md#skills/}"; rel="${rel%/SKILL.md}"
  desc=$(sed -n 's/^description:[[:space:]]*//p' "$skill_md" | head -1 | cut -c1-88)
  printf '%-24s %s\n' "$rel" "$desc"
done < <(find skills -name SKILL.md | sort)
