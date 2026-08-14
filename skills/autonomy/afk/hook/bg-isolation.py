#!/usr/bin/env python3
"""Разрешить фоновой сессии править файлы проекта — и вернуть как было.

    MODE_ARG=on|off STATE_DIR=<каталог состояния> bg-isolation.py <settings.local.json>

Фоновой сессии харнесс по умолчанию запрещает трогать общий чекаут: требует
отдельный worktree. Ночью это бессмысленно — сторож поднимает сессию, чтобы она
продолжила ту же работу в том же дереве, а не увела её в сторону. Прежнее
значение настройки запоминается рядом с состоянием, чтобы утром вернуть его.
"""

import json
import os
import pathlib
import sys


def main() -> int:
    settings = pathlib.Path(sys.argv[1])
    mark = pathlib.Path(os.environ["STATE_DIR"]) / "bg-isolation-was"
    mode = os.environ.get("MODE_ARG", "on")

    try:
        data = json.loads(settings.read_text())
    except Exception:
        data = {}
    worktree = data.get("worktree") or {}

    if mode == "off":
        if not mark.exists():
            return 0
        was = mark.read_text().strip()
        if was == "-":
            worktree.pop("bgIsolation", None)
        else:
            worktree["bgIsolation"] = was
        mark.unlink()
    else:
        if worktree.get("bgIsolation") == "none":
            return 0
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(worktree.get("bgIsolation", "-") + "\n")
        worktree["bgIsolation"] = "none"

    if worktree:
        data["worktree"] = worktree
    else:
        data.pop("worktree", None)

    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
