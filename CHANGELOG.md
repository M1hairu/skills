# Изменения

Версии — у плагина (`.claude-plugin/plugin.json`). Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## 0.2.0 — 2026-08-12

### Добавлено

- Категория `memory` и скилл `vault` — хранилище знаний в `~/vault`: правила ведения
  (главный файл на проект, детали рядом, связи `[[ссылками]]`) и явная граница с машинной
  памятью Claude Code и личным волтом человека, которые скилл не трогает.
- Команда `vault-sync` — синхронизация хранилища с приватным git-репозиторием:
  `--pull` / `--push` / `--status`, `pull --rebase --autostash`, блокировка через `flock`,
  остановка с внятным сообщением при конфликте вместо молчаливой починки.
- Образцы юнитов `vault-sync.service` / `.timer` для получасовой синхронизации на сервере.

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
