# Настройка vault

Скилл хранит знание в приватном git-репозитории, поэтому адрес репозитория он должен
узнать от тебя. Настраивается один раз, конфиг лежит вне этого репозитория и в git
не попадает.

## Конфиг

`~/.config/claude-skills/vault.env`:

```sh
VAULT_DIR=$HOME/vault              # где лежит хранилище
VAULT_REMOTE=...                   # SSH-адрес приватного репозитория (нужен при заведении)
VAULT_BRANCH=main
VAULT_HOST_LABEL=laptop            # чем подписывать коммиты этой машины
```

`VAULT_REMOTE` — обычный адрес репозитория в форме, которую понимает `git clone`: либо
короткая ssh-форма хостинга, либо `ssh://`, либо `https://`.

`VAULT_GIT_NAME` и `VAULT_GIT_EMAIL` можно не задавать — берутся из глобального
git-конфига.

## Первая установка на новой машине

```sh
mkdir -p ~/.config/claude-skills
$EDITOR ~/.config/claude-skills/vault.env      # см. выше
source ~/.config/claude-skills/vault.env
git clone "$VAULT_REMOTE" "$VAULT_DIR"
vault-sync --status
```

Если хранилища ещё нет вообще: завести приватный репозиторий, `git init -b main`
в `~/vault`, первый коммит, `git remote add origin "$VAULT_REMOTE"`, `git push -u origin main`.
Репозиторий обязан быть **приватным**: заметки про свои системы наружу не показывают.

## Автоматическая синхронизация

**На ноутбуке — хуки сессии** в `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "vault-sync --pull --quiet" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "vault-sync --push --quiet" } ] }
    ]
  }
}
```

**На сервере — таймер systemd** (`~/.config/systemd/user/vault-sync.timer` и
одноимённый `.service`), раз в полчаса. Юниты-образцы — в `systemd/` рядом с этим файлом.

**Для человека в Obsidian** — плагин obsidian-git с автокоммитом, либо тот же
`vault-sync` руками. Плагин и хуки не мешают друг другу: оба делают `pull --rebase`
перед отправкой.

## Что синхронизацией НЕ управляется

- `~/.claude/projects/*/memory` — машинная память Claude Code, своя на каждой машине;
- `~/Documents/knowledge` — личный волт человека.

Ни то, ни другое скилл не читает и не трогает.
