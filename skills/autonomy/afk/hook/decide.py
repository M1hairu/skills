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


def read_until(line, start, closer):
    """Содержимое подстановки от `start` до парного `closer` и позиция за ним.

    Считаем вложенные `$(` и уважаем кавычки внутри: `$(echo ")")` закрывается
    не первой попавшейся скобкой.
    """
    depth, quote, i = 1, None, start
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(line):
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
        elif closer == ")" and line.startswith("$(", i):
            depth += 1
            i += 2
            continue
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return line[start:i], i + 1
        i += 1
    return line[start:], len(line)


def read_heredoc_mark(line, i):
    """Ограничитель heredoc после `<<` и позиция за ним, либо (None, i).

    Ограничитель бывает склеен из кусков — `<<E"OF"` для оболочки это `EOF`, —
    поэтому читаем всё слово целиком, снимая кавычки по дороге.
    """
    j = i + 2
    # `<<<` — строка на вход, а не heredoc: тела у неё нет.
    if j < len(line) and line[j] == "<":
        return None, i
    if j < len(line) and line[j] == "-":
        j += 1
    while j < len(line) and line[j] in " \t":
        j += 1
    if j >= len(line):
        return None, i

    parts, quoted = [], False
    while j < len(line) and line[j] not in " \t;&|<>()`":
        if line[j] in "'\"":
            quote = line[j]
            end = line.find(quote, j + 1)
            if end == -1:
                return None, i
            parts.append(line[j + 1:end])
            quoted = True
            j = end + 1
            continue
        if line[j] == "\\" and j + 1 < len(line):
            parts.append(line[j + 1])
            quoted = True
            j += 2
            continue
        parts.append(line[j])
        j += 1

    word = "".join(parts)
    # `1 << 2`, `x << $n`, `<< -8` — это сдвиг, а не ограничитель.
    if not word or (not quoted and (word[0].isdigit() or word[0] in "$-")):
        return None, i
    return word, j


def preprocess(command):
    """Один проход по команде: снять комментарии, тела heredoc и подстановки.

    Всё это раньше делали три независимых прохода по сырой строке, и каждый
    ошибался на кавычках: `echo 'пример $(sudo …)'` отклонялся, а `echo "a << b"
    && rm -rf /etc` — наоборот, проходил целиком, потому что `<<` в кавычках
    принимался за heredoc и остаток строки не проверялся вовсе.

    Возвращает (текст для разбора, команды из подстановок).
    """
    out_lines, nested, pending = [], [], []
    quote = None
    carry = ""

    for raw in command.splitlines():
        # Пока не закрыт heredoc, строки — данные. Кавычка, открытая на прошлой
        # строке, означает, что строка продолжается: heredoc там начаться не мог.
        # Обратный слэш в конце строки тела ничего не склеивает — раньше склейка
        # шла до разбора, ограничитель прилипал к тексту, и остаток скрипта
        # целиком считался телом heredoc.
        if pending and quote is None:
            mark = pending[0]
            if raw == mark or raw.rstrip() == mark or raw.lstrip("\t") == mark:
                pending.pop(0)
            continue

        # Продолжение строки склеиваем здесь, а не заранее: до сюда доходят
        # только настоящие команды, без тел heredoc.
        if carry:
            raw = carry + raw
            carry = ""

        line, i, word_start = [], 0, True
        while i < len(raw):
            ch = raw[i]

            if quote == "'":
                if ch == "'":
                    quote = None
                line.append(ch)
                i += 1
                continue

            if quote == '"':
                if ch == "\\" and i + 1 < len(raw):
                    line.append(ch); line.append(raw[i + 1]); i += 2; continue
                if raw.startswith("$((", i):
                    end = raw.find("))", i)
                    if end == -1:
                        line.append(ch); i += 1; continue
                    arith, deeper = preprocess(raw[i + 3:end])
                    nested.extend(deeper)
                    if "$(" in raw[i + 3:end] or "`" in raw[i + 3:end]:
                        nested.append(arith)
                    line.append(" "); i = end + 2; continue
                if raw.startswith("$(", i):
                    chunk, i = read_until(raw, i + 2, ")")
                    nested.append(chunk); line.append(" "); continue
                if ch == "`":
                    chunk, i = read_until(raw, i + 1, "`")
                    nested.append(chunk); line.append(" "); continue
                if ch == '"':
                    quote = None
                line.append(ch)
                i += 1
                continue

            # вне кавычек
            if ch == "\\" and i + 1 < len(raw):
                line.append(ch); line.append(raw[i + 1]); i += 2; word_start = False; continue
            if ch == "#" and word_start:
                break
            if ch in "'\"":
                quote = ch
                line.append(ch); i += 1; word_start = False; continue
            if raw.startswith("$((", i):
                end = raw.find("))", i)
                if end == -1:
                    # Скобки не закрыты — это не арифметика; съедать остаток
                    # строки нельзя, там могут стоять настоящие команды.
                    line.append(ch); i += 1; word_start = False; continue
                # Внутри арифметики тоже бывает подстановка: `$(( $(команда) ))`.
                arith, deeper = preprocess(raw[i + 3:end])
                nested.extend(deeper)
                if "$(" in raw[i + 3:end] or "`" in raw[i + 3:end]:
                    nested.append(arith)
                line.append(" "); i = end + 2; word_start = False; continue
            if raw.startswith("$(", i):
                chunk, i = read_until(raw, i + 2, ")")
                nested.append(chunk); line.append(" "); word_start = False; continue
            # `<( … )` и `>( … )` — тоже команды, просто отданные как файл.
            if raw.startswith("<(", i) or raw.startswith(">(", i):
                chunk, i = read_until(raw, i + 2, ")")
                nested.append(chunk); line.append(" "); word_start = False; continue
            if ch == "`":
                chunk, i = read_until(raw, i + 1, "`")
                nested.append(chunk); line.append(" "); word_start = False; continue
            # `<<<` — строка на вход. Пропускаем целиком: иначе второй и третий
            # `<` снова читаются как начало heredoc, и его «телом» становится
            # весь остаток скрипта.
            if raw.startswith("<<<", i):
                line.append(" "); i += 3; word_start = True; continue
            if raw.startswith("<<", i):
                mark, after = read_heredoc_mark(raw, i)
                if mark is not None:
                    pending.append(mark)
                    i = after
                    continue
            line.append(ch)
            word_start = ch in " \t;&|()"
            i += 1

        text = "".join(line)
        # Продолжение строки считаем по уже очищенному тексту: комментарий
        # оболочка на следующую строку не продолжает, а склейка до разбора
        # проглатывала следующую команду вместе с ним.
        if quote is None and re.search(r"(?<!\\)(\\\\)*\\$", text):
            carry = text[:-1]
            continue

        out_lines.append(text)

    if carry:
        out_lines.append(carry)

    deeper = []
    for chunk in list(nested):
        text, more = preprocess(chunk)
        deeper.append(text)
        deeper.extend(more)
    return "\n".join(out_lines), deeper


def parse(command):
    """Команда → (список простых команд, пути из редиректов).

    Разбираем построчно: для shlex перевод строки — обычный пробел, поэтому
    многострочный скрипт слипался в одну команду, и `rm -rf /tmp/x` на первой
    строке забирал в «цели удаления» слова со всех остальных.
    """
    text, nested = preprocess(command)
    if nested:
        # Команда внутри `$( … )` — такая же команда, просто написанная внутри
        # другой. Разбираем её отдельной строкой.
        text = "\n".join([text] + nested)

    tokens, buffer = [], ""
    for line in text.split("\n"):
        buffer = buffer + "\n" + line if buffer else line
        lexer = shlex.shlex(buffer, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        # Комментарий у shlex начинается с `#` в любом месте слова, у оболочки —
        # только в начале. Из-за этого `git commit -m fix#12 && rm -rf /etc`
        # обрывался на `fix`, и остаток строки не проверялся вовсе.
        lexer.commenters = ""
        try:
            tokens.extend(list(lexer))
        except ValueError:
            # Кавычка не закрыта — строка продолжается на следующей. Копить
            # буфер бесконечно нельзя: каждая строка перелексируется целиком,
            # и на скрипте с одной незакрытой кавычкой разбор уходит в минуты,
            # то есть хук не отвечает вовсе.
            if buffer.count("\n") < 40:
                continue
            tokens.extend(buffer.split())
        tokens.append("\n")
        buffer = ""
    if buffer:
        tokens.extend(buffer.split())

    # Слова, после которых начинается новая команда: без них
    # `for f in a b; do rm -rf /etc; done` выглядит как одна команда `for`.
    KEYWORDS = {"do", "then", "else", "elif", "{", "}", "!", "(", ")"}
    ENDERS = {"done", "fi", "esac", ";;"}

    commands, current, skip_next = [], [], False
    redirects = []
    for index, token in enumerate(tokens):
        # Комментарии сняты в preprocess — здесь их искать нельзя: shlex уже
        # убрал кавычки, и `git commit -m "#123 правка"` выглядел бы как
        # комментарий, гасящий остаток строки вместе с настоящими командами.
        if skip_next:
            if not token.startswith("-"):
                redirects.append(token)
            skip_next = False
            continue
        if token in (";", "&&", "||", "|", "&", "\n", "|&", ";;") or token in KEYWORDS or token in ENDERS:
            if current:
                commands.append(current)
            current = []
            continue
        # Редирект и его цель — не аргументы команды: иначе `rm -rf build > /dev/null`
        # читается как попытка удалить /dev/null. Но путь запоминаем: `< ключ`
        # и `>> ~/.ssh/authorized_keys` — обращение к файлу, просто не удаление.
        if re.match(r"^\d*(>>?|<|&>|>&)$", token):
            skip_next = True
            continue
        if re.match(r"^\d*(>>?|&>|<)\S", token):
            redirects.append(re.sub(r"^\d*(>>?|&>|<)", "", token))
            continue
        # Номер дескриптора перед оператором (`2>&1`) — не аргумент команды.
        if token.isdigit() and index + 1 < len(tokens) and \
                re.match(r"^(>>?|<|&>|>&)", tokens[index + 1]):
            continue
        current.append(token)
    if current:
        commands.append(current)
    return [c for c in commands if c], redirects


def split_commands(command):
    """Только команды, без путей из редиректов."""
    return parse(command)[0]


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


def expand_vars(path, cwd):
    """Раскрыть переменные, значение которых известно и здесь: `$HOME`, `$PWD`.

    Формы разные — `$HOME`, `${HOME}`, `${HOME:-/home/имя}`, — и все означают одно.
    """
    home = os.path.expanduser("~").rstrip("/")
    known = {"HOME": home, "PWD": cwd, "OLDPWD": cwd,
             "USER": os.path.basename(home), "LOGNAME": os.path.basename(home)}

    def repl(match):
        name = match.group("name")
        return known.get(name, match.group(0))

    path = re.sub(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(:[-=?+][^}]*)?\}", repl, path)
    path = re.sub(r"\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)", repl, path)
    return path


def resolve(path, cwd):
    path = expand_vars(os.path.expanduser(path.strip("'\"")), cwd)
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
        path = expand_vars(arg.strip("'\""), cwd)
        if "$" in path:
            # Осталась чужая переменная. Пропускать весь путь нельзя: в
            # `/home/$USER/Документы` и `/etc/$X` видимая часть уже говорит, куда
            # метит команда. Проверяем этот префикс; если он пуст или относителен,
            # цель действительно неизвестна — не выдумываем.
            head = path.split("$", 1)[0]
            if not head.startswith(("/", "~")):
                continue
            path = head
        out.append(resolve(path, cwd))
    return out


# Команды, которые ничего не читают и не отправляют: путь в их аргументах —
# текст, а не обращение к файлу. Без этого ночью не написать ни теста, ни строки
# документации, где такой путь просто упомянут.
PRINTERS = {"echo", "printf", ":", "true", "false"}


def secret_tokens(command):
    """Слова команды, в которых путь к ключам означает обращение к ним.

    Печатающие команды пропускаем — напечатать путь не то же самое, что его
    прочитать, — а вот файл из редиректа считаем: `curl -d @- < ключ` читает
    его так же, как `cat`.
    """
    commands, redirects = parse(command)
    out = list(redirects)
    for words in commands:
        name, args = peel(words)
        if not name or name in PRINTERS:
            continue
        out.append(name)
        out.extend(args)
    return out


def secret_hit(tokens):
    """Первое слово, которым трогают ключи или учётные данные.

    Временные каталоги пропускаем: настоящих ключей там нет, а тестовые стенды
    и распакованные архивы живут именно в них — иначе ночью не проверить
    собственную установку.
    """
    for raw in tokens:
        token = str(raw).strip("'\"")
        if re.match(r"^(/tmp|/var/tmp)/", os.path.expanduser(token)):
            continue
        for pattern in SECRETS:
            if re.search(pattern, token):
                return token
        # `.env` ловим по имени файла, а не по подстроке: `afk.env` и `prod.env` —
        # те же секреты, а `process.env` в коде — не файл. Только для путей, про
        # которые видно, куда они ведут: у `$КАТАЛОГ/имя.env` это неизвестно,
        # и отклонять его — гадание.
        if os.path.expanduser(token).startswith("/") and \
                os.path.basename(token).endswith(".env"):
            return token
    return None


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

        if name in ("cd", "pushd") and args:
            here = resolve(args[0], here)
            continue

        # Хранилища секретов: печатать токен в терминал ночью незачем.
        if (name == "gh" and args[:2] == ["auth", "token"]) or \
           name in ("secret-tool", "keyring") or \
           (name == "pass" and "show" in args) or \
           (name == "security" and "find-generic-password" in args):
            return "deny", "выдача учётных данных"

        # Отправка файла наружу: curl/wget, которым скормили файл за пределами
        # проекта. Формы разные — `-d @файл`, `-T файл`, `--post-file=файл`, —
        # и каждая пишется ночью не задумываясь.
        if name in ("curl", "wget"):
            # Только флаги, которые сами по себе означают файл. `-d /путь` без `@`
            # отправляет строку, а не содержимое, и отклонять его — врать человеку.
            takes_file = {"-T", "--upload-file", "--post-file", "--body-file",
                          "-K", "--config"}
            candidates, expect_file = [], False
            for arg in args:
                if expect_file:
                    candidates.append(arg)
                    expect_file = False
                    continue
                if arg in takes_file:
                    expect_file = True
                    continue
                if arg.startswith("--") and "=" in arg:
                    flag, value = arg.split("=", 1)
                    if flag in takes_file:
                        candidates.append(value)
                        continue
                if "@" in arg:
                    candidates.append(arg.split("@", 1)[1])

            for candidate in candidates:
                candidate = candidate.split("@", 1)[-1]
                if not candidate or candidate.startswith(("http", "-")):
                    continue
                if os.path.sep not in candidate:
                    continue
                target = resolve(candidate, here)
                if (not inside(target, project)
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
            # Переменная плюс переход вверх — путь может выйти куда угодно
            # (`/tmp/$X/../../home/имя`), и видимый префикс тут не помогает.
            for arg in args:
                if "$" in arg and ".." in arg:
                    return "deny", f"путь с переменной и переходом вверх ({arg})"
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

        if name == "find" and ("-delete" in args or
                               ("-exec" in args and any(os.path.basename(a) in DELETERS for a in args))):
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

        tokens = path_like(data).split()
    else:
        tokens = [str(data.get(key, "")) for key in ("file_path", "notebook_path", "path")]
        if data.get("command"):
            tokens += secret_tokens(str(data["command"]))

    if secret_hit(tokens):
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
