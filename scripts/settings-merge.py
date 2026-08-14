#!/usr/bin/env python3
"""Правила разрешений и хуки скилла в настройках Claude Code.

    settings-merge.py permissions <permissions.json> <settings.json> add|remove
    settings-merge.py hooks       <hooks.json>       <settings.json> add|remove

Печатает «да», если файл настроек изменился, и молчит, если всё уже на месте.
Вызывается из install.sh; руками обычно не нужен.
"""

import json
import sys


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as err:
        print(f"{path}: не разобрать JSON ({err}) — не трогаю", file=sys.stderr)
        raise SystemExit(1)


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def merge_permissions(rules, data, mode):
    if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
        print("ожидался список строк-правил", file=sys.stderr)
        raise SystemExit(2)

    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    if mode == "add":
        allow.extend(rule for rule in rules if rule not in allow)
    else:
        allow[:] = [rule for rule in allow if rule not in rules]
        if not allow:
            data["permissions"].pop("allow", None)
        if not data.get("permissions"):
            data.pop("permissions", None)


def merge_hooks(rules, data, mode):
    if not isinstance(rules, dict):
        print("ожидался объект вида {событие: [группы хуков]}", file=sys.stderr)
        raise SystemExit(2)

    hooks = data.setdefault("hooks", {})
    for event, groups in rules.items():
        current = hooks.setdefault(event, [])
        for group in groups:
            same = [g for g in current if json.dumps(g, sort_keys=True) == json.dumps(group, sort_keys=True)]
            if mode == "add" and not same:
                current.append(group)
            elif mode == "remove":
                for g in same:
                    current.remove(g)
        if not current:
            hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)


def main() -> int:
    kind, rules_file, settings_file, mode = sys.argv[1:5]

    rules = load(rules_file, None)
    if rules is None:
        return 0

    data = load(settings_file, {})
    before = json.dumps(data, sort_keys=True)

    if kind == "permissions":
        merge_permissions(rules, data, mode)
    elif kind == "hooks":
        merge_hooks(rules, data, mode)
    else:
        print(f"неизвестный раздел настроек: {kind}", file=sys.stderr)
        return 2

    if json.dumps(data, sort_keys=True) == before:
        return 0

    save(settings_file, data)
    print("да")
    return 0


if __name__ == "__main__":
    sys.exit(main())
