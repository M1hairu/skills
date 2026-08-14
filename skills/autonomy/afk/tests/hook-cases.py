#!/usr/bin/env python3
"""Что ночной хук пропускает, а что отклоняет.

    hook-cases.py [путь к afk-autonomy]

Каталог автономии подсовывается временный, поэтому проверка не зависит от того,
включён ли режим на машине. Ненулевой код возврата — есть промахи.
"""

import json
import os
import subprocess
import sys
import tempfile

HOOK = sys.argv[1] if len(sys.argv) > 1 else "afk-autonomy"
PROJECT = "/tmp/проект"
HOME = os.path.expanduser("~").rstrip("/")
KEY = f"{HOME}/.s" + "sh/id_" + "ed25519"

# (ожидание, инструмент, аргументы, о чём случай)
CASES = [
    ("allow", "Bash", {"command": "npm test && git commit -am wip"}, "обычная работа"),
    ("allow", "Bash", {"command": f"cd {PROJECT}/sub && rm -rf /tmp/tmp.abc"}, "cd, потом rm во временных"),
    ("allow", "Bash", {"command": f"ls {HOME} && rm -rf build"}, "rm внутри проекта"),
    ("allow", "Bash", {"command": "grep -rn 'sudo pacman' README.md"}, "слово sudo как текст"),
    ("allow", "Bash", {"command": "echo 'shutdown это плохо' >> notes.md"}, "слово shutdown в тексте"),
    ("allow", "Bash", {"command": "git push origin feature/x"}, "обычный пуш"),
    ("allow", "Write", {"file_path": f"{PROJECT}/notes.md", "content": f"путь {KEY} в тексте"},
     "путь к ключу внутри содержимого"),
    ("deny", "Bash", {"command": "sudo pacman -S tmux"}, "запуск от root"),
    ("deny", "Bash", {"command": "cd /tmp && sudo rm -rf /var/log"}, "тот же запуск после cd"),
    ("deny", "Bash", {"command": "rm -rf /etc/foo"}, "удаление вне проекта"),
    ("deny", "Bash", {"command": f"rm -rf {HOME}/Документы"}, "удаление в домашнем каталоге"),
    ("deny", "Bash", {"command": "git push --force origin main"}, "форс-пуш"),
    ("deny", "Bash", {"command": "systemctl poweroff"}, "выключение машины"),
    ("deny", "Read", {"file_path": KEY}, "чтение ключа"),
]


def decide(state_dir, cwd, tool, args):
    event = json.dumps({"cwd": cwd, "tool_name": tool, "tool_input": args})
    env = dict(os.environ, XDG_STATE_HOME=state_dir)
    out = subprocess.run([HOOK, "--hook"], input=event, env=env,
                         capture_output=True, text=True).stdout.strip()
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
            print(f"  {'ок' if ok else 'ПРОМАХ':<7} {expect:<5} → {got:<7} {title}")

        got = decide(state, "/opt/чужой", "Bash", {"command": "rm -rf /"})
        ok = got == "молчит"
        bad += not ok
        print(f"  {'ок' if ok else 'ПРОМАХ':<7} вне автономных каталогов хук молчит")

    print(f"  промахов: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
