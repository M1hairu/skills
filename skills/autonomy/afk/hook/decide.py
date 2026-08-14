#!/usr/bin/env python3
"""Решение ночного хука: пускать вызов инструмента или отклонить.

Событие приходит на stdin, вердикт уходит в stdout. Молчание означает «решай как
обычно» — так хук ведёт себя днём и в каталогах, где ночной режим не включён.

Это подстраховка от очевидной глупости, а не песочница: команду можно записать
так, что разбор её не узнает. Настоящая граница — правила в afk-policy.md и то,
что ночная сессия работает в своём проекте.
"""

import json
import os
import re
import shlex
import sys

# Файлы и каталоги, в которые ночью не ходят.
SECRETS = (
    r"\.ssh\b", r"\.gnupg\b", r"\.aws\b", r"\.netrc\b", r"\.git-credentials\b",
    r"\.credentials\.json\b", r"id_(rsa|ed25519|ecdsa)\b", r"\.env(\b|$)",
)

# Обёртки, за которыми прячется настоящая команда.
WRAPPERS = {"env", "nice", "ionice", "time", "nohup", "stdbuf", "setsid",
            "command", "builtin", "exec", "xargs", "sudo", "doas"}

ROOT_RUNNERS = {"sudo", "doas", "pkexec", "runuser"}
POWER = {"shutdown", "reboot", "halt", "poweroff"}
DELETERS = {"rm", "shred", "srm"}


def emit(decision, reason, event, state_dir):
    """Вердикт в stdout, отказ — ещё и в журнал каталога автономии."""
    if decision != "allow":
        try:
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "hook.log"), "a") as log:
                log.write("{}\t{}\t{}\n".format(decision, event.get("tool_name", "?"), reason))
        except Exception:
            pass
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))
    sys.exit(0)


def watched_dirs(dirs_file):
    try:
        with open(dirs_file) as f:
            return [line.strip() for line in f if line.strip()]
    except Exception:
        return []


def strip_heredocs(command):
    """Тело heredoc — данные, а не команды: их разбирать нельзя."""
    out, skip_until = [], None
    for line in command.splitlines():
        if skip_until is not None:
            if line.strip() == skip_until:
                skip_until = None
            continue
        match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if match:
            skip_until = match.group(1)
            line = line[:match.start()]
        out.append(line)
    return "\n".join(out)


def split_commands(command):
    """Команда → список списков токенов, по одному на каждую простую команду."""
    lexer = shlex.shlex(strip_heredocs(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        # Незакрытая кавычка: разбираем грубо, но лучше так, чем никак.
        tokens = strip_heredocs(command).split()

    commands, current = [], []
    for token in tokens:
        if token in (";", "&&", "||", "|", "&", "\n", "|&"):
            if current:
                commands.append(current)
            current = []
            continue
        current.append(token.strip("(){}"))
    if current:
        commands.append(current)
    return [c for c in commands if c]


def peel(words):
    """Снять обёртки и присваивания переменных, вернуть (имя команды, аргументы)."""
    i = 0
    while i < len(words):
        word = words[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            i += 1
            continue
        name = os.path.basename(word)
        if name in WRAPPERS and i + 1 < len(words):
            if name in ROOT_RUNNERS:
                return name, words[i + 1:]
            i += 1
            continue
        return name, words[i + 1:]
    return "", []


def resolve(path, cwd):
    path = os.path.expanduser(path.strip("'\""))
    if path.startswith("$HOME"):
        path = os.path.expanduser("~") + path[len("$HOME"):]
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.normpath(path)


def inside(path, base):
    base = base.rstrip("/")
    return path == base or path.startswith(base + "/")


def deletion_targets(args, cwd):
    """Пути, которые команда удаляет, без флагов."""
    return [resolve(a, cwd) for a in args if not a.startswith("-")]


def check_bash(command, cwd, home):
    """Вернуть (решение, причина) или None, если возражений нет.

    `project` — каталог сессии, единственное место, где ночью можно удалять.
    `here` меняется вслед за `cd` и нужен только чтобы понять, куда указывает
    относительный путь: `cd /etc && rm -rf apache2` метит вовсе не в проект.
    """
    scratch = ("/tmp", "/var/tmp", os.path.join(home, ".cache"))
    project = cwd
    here = cwd

    for words in split_commands(command):
        name, args = peel(words)
        if not name:
            continue

        if name in ROOT_RUNNERS:
            return "deny", "запуск от root без человека"
        if name in POWER or (name == "systemctl" and set(args) & POWER):
            return "deny", "выключение машины"
        if name == "mkfs" or name.startswith("mkfs."):
            return "deny", "разметка устройства"
        if name == "dd" and any(a.startswith("of=/dev/") for a in args):
            return "deny", "запись на устройство"

        if name == "cd" and args:
            here = resolve(args[0], here)
            continue

        # Хранилища секретов: печатать токен в терминал ночью незачем.
        if (name == "gh" and args[:2] == ["auth", "token"]) or \
           name in ("secret-tool", "keyring") or \
           (name == "pass" and "show" in args) or \
           (name == "security" and "find-generic-password" in args):
            return "deny", "выдача учётных данных"

        # Отправка файла наружу: curl/wget с @файлом за пределами проекта.
        if name in ("curl", "wget"):
            for arg in args:
                if "@" not in arg:
                    continue
                candidate = arg.split("@", 1)[1]
                if not candidate or candidate.startswith(("http", "-")):
                    continue
                target = resolve(candidate, here)
                if os.path.sep in candidate and not inside(target, project):
                    return "deny", f"отправка наружу файла вне проекта ({target})"

        if name == "git":
            flat = " ".join(args)
            if re.search(r"\bpush\b", flat):
                forced = re.search(r"(^|\s)(--force|-f)(\s|$)", flat) and "--force-with-lease" not in flat
                # `+ветка` в refspec — тот же форс, только без слова.
                if forced or re.search(r"(^|\s)\+\S+", flat) or "--delete" in args:
                    return "deny", "форс-пуш или снос ветки"
            if "clean" in args and any(a.startswith("-") and "x" in a and "f" in a for a in args):
                return "deny", "git clean -xf сносит незакоммиченное"

        recursive = any(a in ("-r", "-R", "--recursive") or
                        (a.startswith("-") and not a.startswith("--") and re.search(r"[rR]", a))
                        for a in args)

        if name in DELETERS and (recursive or name != "rm"):
            for target in deletion_targets(args, here):
                if any(inside(target, s) for s in scratch):
                    continue
                # Домашний каталог и его прямые потомки — не «файлы проекта»,
                # даже когда сессия запущена прямо из ~.
                if target == home or (inside(target, home)
                                      and target.count("/") - home.count("/") <= 1
                                      and not inside(project, target)):
                    return "deny", f"рекурсивное удаление в домашнем каталоге ({target})"
                if not inside(target, project):
                    return "deny", f"рекурсивное удаление вне проекта ({target})"

        if name == "find" and ("-delete" in args or ("-exec" in args and "rm" in " ".join(args))):
            root = deletion_targets([a for a in args if not a.startswith("-")][:1], here)
            if root and not inside(root[0], project) and not any(inside(root[0], s) for s in scratch):
                return "deny", f"массовое удаление вне проекта ({root[0]})"

        if name == "rsync" and "--delete" in args:
            targets = [resolve(a, here) for a in args if not a.startswith("-")]
            if targets and not inside(targets[-1], project) and not any(inside(targets[-1], s) for s in scratch):
                return "deny", f"rsync --delete вне проекта ({targets[-1]})"

    return None


def main() -> int:
    dirs_file = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0

    cwd = event.get("cwd") or os.getcwd()
    home = os.path.expanduser("~").rstrip("/")

    watched = [d for d in watched_dirs(dirs_file) if inside(cwd, d)]
    if not watched:
        return 0
    state_dir = os.path.join(max(watched, key=len), ".afk")

    tool = event.get("tool_name", "")
    data = event.get("tool_input") or {}

    # У встроенных инструментов смотрим на то, с чем они работают: путь и команду.
    # Содержимое не трогаем — иначе ночью не написать ни скрипта, ни документации,
    # где такой путь просто упомянут. У инструментов MCP имена полей заранее
    # неизвестны, поэтому там приходится смотреть на всё подряд.
    if tool.startswith("mcp__"):
        paths = json.dumps(data, ensure_ascii=False)
    else:
        paths = " ".join(str(data.get(key, "")) for key in
                         ("file_path", "notebook_path", "path", "command"))
    for pattern in SECRETS:
        if re.search(pattern, paths):
            emit("deny", "ключи и учётные данные ночью не трогаем — запиши в BLOCKED.md",
                 event, state_dir)

    if tool == "Bash":
        verdict = check_bash(str(data.get("command", "")), cwd, home)
        if verdict:
            emit(verdict[0], verdict[1] + " — опиши в BLOCKED.md и иди дальше", event, state_dir)

    emit("allow", "режим AFK: разрешения не останавливают работу", event, state_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
