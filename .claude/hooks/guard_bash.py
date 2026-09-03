#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash): deny pushes and history rewrites.

Emits a permissionDecision on stdout and exits 0. Exit code 2 with a message on
stderr would work too, but the JSON form lets us give the model a reason it can
act on.
"""

from __future__ import annotations

import json
import re
import sys

DENY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bgit\b[^|;&]*\bpush\b"),
     "Pushing is reserved for the human. Stage and commit only; "
     "tell the user the branch is ready and let them run `git push`."),
    (re.compile(r"\bgit\b[^|;&]*\bpush\b[^|;&]*(--force|-f\b)"),
     "Force-push is never allowed from an agent session."),
    (re.compile(r"\bgit\b[^|;&]*\breset\b[^|;&]*--hard"),
     "`git reset --hard` destroys uncommitted work. Ask the user instead."),
    (re.compile(r"\bgit\b[^|;&]*\bcommit\b[^|;&]*(--no-verify|-n\b)"),
     "Do not bypass pre-commit hooks; they run the secret scan."),
    (re.compile(r"\b(cat|type|less|head|tail|bat)\b[^|;&]*\.env\b"),
     "`.env` files are out of bounds. Use `.env.example` if you need the schema."),
]


def split_commands(command: str) -> list[str]:
    return [p.strip() for p in re.split(r"&&|\|\||;|\n|\|", command) if p.strip()]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    for segment in split_commands(command):
        for pattern, reason in DENY_RULES:
            if pattern.search(segment):
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }))
                return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
