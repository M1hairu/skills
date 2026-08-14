#!/usr/bin/env python3
"""Что ночной хук пропускает, а что отклоняет.

    hook-cases.py [путь к afk-hook]

Каталог автономии подсовывается временный, поэтому проверка не зависит от того,
включён ли режим на машине. Ненулевой код возврата — есть промахи.

Случаи собраны из разбора критика: не только те формы, под которые написаны
правила, но и обходы (префиксы, обёртки, относительные пути после cd) и обычная
работа, которую хук не должен трогать.
"""

import json
import os
import subprocess
import sys
import tempfile

HOOK = sys.argv[1] if len(sys.argv) > 1 else "afk-hook"
PROJECT = "/tmp/проект"
HOME = os.path.expanduser("~").rstrip("/")
KEY = f"{HOME}/.s" + "sh/id_" + "ed25519"

# (ожидание, инструмент, аргументы, о чём случай)
CASES = [
    # обычная ночная работа — хук не должен мешать
    ("allow", "Bash", {"command": "npm test && git commit -am wip"}, "обычная работа"),
    ("allow", "Bash", {"command": f"cd {PROJECT}/sub && rm -rf /tmp/tmp.abc"}, "удаление во временных"),
    ("allow", "Bash", {"command": "rm -rf build"}, "удаление внутри проекта"),
    ("allow", "Bash", {"command": f"rm -rf {HOME}/.cache/проект"}, "чистка кэша сборки"),
    ("allow", "Bash", {"command": "rm -rf /var/tmp/build-123"}, "чистка /var/tmp"),
    ("allow", "Bash", {"command": "git commit -m 'чиню; sudo больше не нужен'"}, "запретное слово в кавычках"),
    ("allow", "Bash", {"command": "grep -rn 'sudo pacman' README.md"}, "поиск по своему коду"),
    ("allow", "Bash", {"command": "cat > setup.sh <<'EOF'\nsudo pacman -S tmux\nEOF"}, "heredoc со скриптом"),
    ("allow", "Bash", {"command": "git push origin feature/x"}, "обычный пуш"),
    ("allow", "Bash", {"command": "git push --force-with-lease origin main"}, "безопасный форс"),
    ("allow", "Grep", {"pattern": "id_rsa"}, "поиск упоминаний ключа в коде"),
    ("allow", "Write", {"file_path": f"{PROJECT}/notes.md", "content": f"путь {KEY} в тексте"},
     "путь к ключу в содержимом файла"),

    # то, чего ночью быть не должно
    ("deny", "Bash", {"command": "sudo pacman -S tmux"}, "запуск от root"),
    ("deny", "Bash", {"command": "env sudo rm -rf /var/log"}, "root через обёртку env"),
    ("deny", "Bash", {"command": "/usr/bin/sudo rm -rf /var/log"}, "root по полному пути"),
    ("deny", "Bash", {"command": "LANG=C sudo rm -rf /var/log"}, "root после присваивания"),
    ("deny", "Bash", {"command": "echo x | xargs sudo rm -rf /var/log"}, "root через xargs"),
    ("deny", "Bash", {"command": "cd /etc && rm -rf apache2"}, "cd наружу, потом удаление"),
    ("deny", "Bash", {"command": "cd ~ && rm -rf Documents"}, "cd в дом, потом удаление"),
    ("deny", "Bash", {"command": "rm -rf /etc/foo"}, "удаление вне проекта"),
    ("deny", "Bash", {"command": f"rm -rf {HOME}/Документы"}, "удаление в домашнем каталоге"),
    ("deny", "Bash", {"command": "rm --recursive --force /etc/foo"}, "длинные флаги"),
    ("deny", "Bash", {"command": "/bin/rm -rf /etc/foo"}, "полный путь к rm"),
    ("deny", "Bash", {"command": "rm -rf $HOME/Documents"}, "путь через переменную HOME"),
    ("deny", "Bash", {"command": "find /etc -name '*.conf' -delete"}, "массовое удаление через find"),
    ("deny", "Bash", {"command": f"rsync -a --delete /tmp/пусто/ {HOME}/Документы/"}, "rsync --delete наружу"),
    ("deny", "Bash", {"command": "git push --force origin main"}, "форс-пуш"),
    ("deny", "Bash", {"command": "git push origin +main"}, "форс через refspec"),
    ("deny", "Bash", {"command": "git push --delete origin main"}, "снос ветки"),
    ("deny", "Bash", {"command": "systemctl poweroff"}, "выключение машины"),
    ("deny", "Bash", {"command": "mkfs.ext4 /dev/sda1"}, "разметка устройства"),
    ("deny", "Bash", {"command": f"cp -r {HOME}/.s" + "sh /tmp/backup"}, "копирование каталога с ключами"),
    ("deny", "Read", {"file_path": KEY}, "чтение ключа"),
]


def decide(state_dir, cwd, tool, args):
    event = json.dumps({"cwd": cwd, "tool_name": tool, "tool_input": args})
    env = dict(os.environ, XDG_STATE_HOME=state_dir)
    out = subprocess.run([HOOK], input=event, env=env, capture_output=True, text=True).stdout.strip()
    if not out:
        return "молчит"
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"]


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as state:
        os.makedirs(os.path.join(state, "afk"), exist_ok=True)
        with open(os.path.join(state, "afk", "autonomy-dirs"), "w") as f:
            f.write(PROJECT + "\n")

        for expect, tool, args, title in CASES:
            got = decide(state, PROJECT, tool, args)
            ok = got == expect
            bad += not ok
            if not ok:
                print(f"  ПРОМАХ  ждали {expect:<5} получили {got:<7} {title}")

        got = decide(state, "/opt/чужой", "Bash", {"command": "rm -rf /"})
        if got != "молчит":
            bad += 1
            print(f"  ПРОМАХ  вне автономных каталогов хук должен молчать, а сказал {got}")

    print(f"  случаев: {len(CASES) + 1}, промахов: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
