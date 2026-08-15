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

# Файлы и каталоги, в которые ночью не ходят. Перед именем требуется граница пути:
# иначе `process.env` читается как файл с секретами, а `docs.aws.amazon.com` — как
# каталог с ключами, и обычная работа встаёт на ровном месте.
EDGE = r"(?<![\w.-])"
SECRETS = (
    EDGE + r"\.ssh\b", EDGE + r"\.gnupg\b", EDGE + r"\.aws\b", EDGE + r"\.netrc\b",
    EDGE + r"\.git-credentials\b", EDGE + r"\.credentials\.json\b",
    # Имя ключа считается обращением к нему только в составе пути: в тексте
    # (тест, документация, сообщение коммита) это просто слово.
    r"(?<=/)id_(rsa|ed25519|ecdsa)\b", EDGE + r"\.env(\b|$)",
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
    """Команда → список списков токенов, по одному на каждую простую команду.

    Разбираем построчно: для shlex перевод строки — обычный пробел, поэтому
    многострочный скрипт слипался в одну команду, и `rm -rf /tmp/x` на первой
    строке забирал в «цели удаления» слова со всех остальных.
    """
    text = re.sub(r"\\\n", " ", strip_heredocs(command))
    tokens, buffer = [], ""
    for line in text.split("\n"):
        buffer = buffer + "\n" + line if buffer else line
        lexer = shlex.shlex(buffer, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens.extend(list(lexer))
        except ValueError:
            # Кавычка не закрыта — строка продолжается на следующей.
            continue
        tokens.append("\n")
        buffer = ""
    if buffer:
        tokens.extend(buffer.split())

    commands, current, skip_next = [], [], False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in (";", "&&", "||", "|", "&", "\n", "|&"):
            if current:
                commands.append(current)
            current = []
            continue
        # Редирект и его цель — не аргументы команды: иначе `rm -rf build > /dev/null`
        # читается как попытка удалить /dev/null.
        if re.match(r"^\d*(>>?|<|&>|>&)$", token):
            skip_next = True
            continue
        if re.match(r"^\d*(>>?|&>)\S", token):
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
    for form in ("${HOME}", "$HOME"):
        if path.startswith(form):
            path = os.path.expanduser("~") + path[len(form):]
            break
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.normpath(path)


def inside(path, base):
    base = base.rstrip("/")
    return path == base or path.startswith(base + "/")


def deletion_targets(args, cwd):
    """Пути, которые команда удаляет, без флагов.

    Путь с нераскрытой переменной пропускаем: куда он указывает, знает оболочка,
    а не мы, и `rm -rf "$BUILD"` — обычная строчка ночного скрипта. Исключение —
    `$HOME`: там путь известен, и правило про домашний каталог должно работать.
    """
    out = []
    for arg in args:
        if arg.startswith("-"):
            continue
        if "$" in arg and not arg.startswith("$HOME") and "${HOME}" not in arg:
            continue
        out.append(resolve(arg, cwd))
    return out


def check_bash(command, cwd, home):
    """Вернуть (решение, причина) или None, если возражений нет.

    `project` — каталог сессии, единственное место, где ночью можно удалять.
    `here` меняется вслед за `cd` и нужен только чтобы понять, куда указывает
    относительный путь: `cd /etc && rm -rf apache2` метит вовсе не в проект.
    """
    # Кэши сборщиков — расходный материал: ночная сборка должна уметь их снести,
    # иначе упавший кэш чинить некому.
    scratch = ["/tmp", "/var/tmp"] + [os.path.join(home, name) for name in (
        ".cache", ".npm", ".yarn", ".gradle", ".m2", ".cargo", ".pub-cache", ".ivy2",
        ".gem", ".nuget", ".bun", ".deno", ".pnpm-store")]
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
                if (os.path.sep in candidate and not inside(target, project)
                        and not any(inside(target, s) for s in scratch)):
                    return "deny", f"отправка наружу файла вне проекта ({target})"

        if name == "git":
            # Подкоманда — первый аргумент, который не флаг и не его значение:
            # искать «push» по всей строке нельзя, слово попадается в сообщениях коммитов.
            rest, subcommand = list(args), ""
            while rest:
                head = rest.pop(0)
                if head in ("-C", "-c", "--git-dir", "--work-tree"):
                    rest and rest.pop(0)
                    continue
                if head.startswith("-"):
                    continue
                subcommand = head
                break

            if subcommand == "push":
                forced = any(a in ("--force", "-f") for a in rest)
                if forced or any(a.startswith("+") for a in rest) or "--delete" in rest:
                    return "deny", "форс-пуш или снос ветки"
            if subcommand == "clean" and any(a.startswith("-") and "x" in a and "f" in a for a in rest):
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

        if name == "find" and ("-delete" in args or ("-exec" in args and "rm" in args)):
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
        # Из всего ввода берём только то, что похоже на путь: адреса и куски кода
        # («process.env», «docs.aws.amazon.com») путями не являются.
        def path_like(value):
            if isinstance(value, dict):
                return " ".join(path_like(v) for v in value.values())
            if isinstance(value, list):
                return " ".join(path_like(v) for v in value)
            text = str(value)
            if re.match(r"^[a-z][a-z0-9+.-]*://", text):
                return ""
            return " ".join(w for w in text.split() if w.startswith(("/", "~", ".")) or "/" in w)

        paths = path_like(data)
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
