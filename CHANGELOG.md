# Изменения

Версии — у плагина (`.claude-plugin/plugin.json`). Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## 0.1.0 — 2026-08-12

Первый выпуск.

### Добавлено

- Скиллы `afk` и `vps-afk` в категории `autonomy`.
- `install.sh` — установка симлинками: выбор скиллов (списком, по именам или `-i`), выбор
  области (`--local` / `--project` против глобальной), `--agents` для `~/.agents/skills`,
  `--dry-run`, `--force` с бэкапом, `--uninstall`.
- Плагин Claude Code: `.claude-plugin/plugin.json` и `.claude-plugin/marketplace.json`.
- `scripts/validate.sh` — фронтматтер, скрипты, инварианты репозитория и поиск личных данных.
- `scripts/new-skill.sh`, `scripts/list-skills.sh`, шаблон скилла.
- Документация: `docs/autonomy/*`, соглашения в `CLAUDE.md` и `.agents/`.
