#!/usr/bin/env python3
"""Stop hook: get Claude to commit its own work first, fall back to a direct commit.

Strategy (方案 B 為主, 方案 A 當 fallback):
  - B (primary): if uncommitted, non-blocked changes exist, print instructions to
    stderr and exit 2. Exit code 2 on a Stop hook blocks the stop and re-prompts
    Claude with the stderr text, so the running session writes its own
    Conventional Commit and commits it. A per-session attempt counter is kept in
    .git/claude-autocommit-state.json (reset once the tree is clean).
  - A (fallback): once B has been asked MAX_B_ATTEMPTS times in a row and changes
    are STILL uncommitted, commit directly here with a haiku-generated message
    and exit 0, so the turn can actually end.

Contract:
  - reads the Stop hook payload from stdin (session_id, transcript_path, cwd,
    stop_hook_active).
  - exits 2 only for the deliberate "please commit yourself" request above;
    every other path exits 0 so the session is never stuck looping.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# --- configuration -----------------------------------------------------------

RECURSION_GUARD = "CLAUDE_AUTOCOMMIT_RUNNING"
MODEL = os.environ.get("CLAUDE_AUTOCOMMIT_MODEL", "haiku")
MAX_DIFF_CHARS = 12_000
LOCK_STALE_SECONDS = 300
MAX_B_ATTEMPTS = 2
STATE_STALE_SECONDS = 3600

# Files that must never reach the index, even if git-tracked by mistake.
BLOCKED_GLOBS = [
    ".env", ".env.*", "*.env",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks",
    "id_rsa*", "id_ed25519*",
    "*credentials*.json", "*service-account*.json",
    "secrets.*", "*.secrets.*",
    ".claude/settings.local.json",
    "*.sqlite3", "*.db",
    "__pycache__", "__pycache__/*", "*.pyc", "*.pyo",
]

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|passwd|password|access[_-]?token)\b\s*[:=]\s*"
        r"['\"][^'\"]{12,}['\"]"
    ),
]

CWD = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
STATE_FILE = CWD / ".git" / "claude-autocommit-state.json"

# Windows consoles often default stdout/stderr to a non-UTF-8 codepage, which
# mangles any non-ASCII character in text fed back to Claude via the Stop hook.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


# --- helpers -----------------------------------------------------------------


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=CWD, capture_output=True, text=True, encoding="utf-8"
    )


def log(msg: str) -> None:
    print(f"[auto-commit] {msg}")


def in_git_repo() -> bool:
    return git("rev-parse", "--is-inside-work-tree").returncode == 0


def mid_operation() -> bool:
    """True during merge / rebase / cherry-pick / bisect."""
    git_dir = git("rev-parse", "--git-dir").stdout.strip()
    if not git_dir:
        return True
    base = (CWD / git_dir).resolve()
    markers = ["MERGE_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply",
               "CHERRY_PICK_HEAD", "BISECT_LOG"]
    return any((base / m).exists() for m in markers)


def is_blocked(path: str) -> bool:
    name = Path(path).name
    return any(
        fnmatch.fnmatch(name, g) or fnmatch.fnmatch(path, g) for g in BLOCKED_GLOBS
    )


def collect_paths() -> tuple[list[str], list[str]]:
    """Return (paths_to_stage, blocked_paths) from the working tree."""
    out = git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    entries = [e for e in out.split("\0") if e]
    keep, blocked = [], []
    i = 0
    while i < len(entries):
        entry = entries[i]
        status, path = entry[:2], entry[3:]
        if status[0] == "R":  # rename: next record is the old path
            i += 1
        i += 1
        if is_blocked(path):
            blocked.append(path)
        else:
            keep.append(path)
    return keep, blocked


def staged_secret_hits() -> list[str]:
    diff = git("diff", "--cached", "--unified=0").stdout
    hits = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(line):
                hits.append(pat.pattern)
                break
    return sorted(set(hits))


def last_user_prompt(transcript_path: str | None) -> str:
    if not transcript_path or not Path(transcript_path).exists():
        return ""
    prompt = ""
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "user":
                continue
            content = rec.get("message", {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    b.get("text", "") for b in content if b.get("type") == "text"
                )
            else:
                continue
            text = text.strip()
            # skip tool results and slash-command noise
            if text and not text.startswith("<"):
                prompt = text
    return prompt[:600]


def generate_message(user_prompt: str) -> str:
    diff = git("diff", "--cached").stdout[:MAX_DIFF_CHARS]
    files = git("diff", "--cached", "--name-only").stdout.strip()
    fallback = f"chore: update {len(files.splitlines())} file(s)"

    prompt = f"""Write a Conventional Commits message for the staged diff below.

Rules:
- Output ONLY the commit message. No markdown fences, no commentary.
- Subject line: <type>(<scope>): <summary>, imperative mood, max 72 chars.
- Types: feat, fix, refactor, perf, test, docs, build, chore.
- Add a body of at most 3 bullet points ONLY if the change is non-trivial.
- English only.

User request that triggered this change:
{user_prompt or "(unknown)"}

Changed files:
{files}

Diff (may be truncated):
{diff}
"""
    env = dict(os.environ, **{RECURSION_GUARD: "1"})
    claude_bin = shutil.which("claude") or "claude"
    try:
        res = subprocess.run(
            [claude_bin, "-p", "--model", MODEL],
            input=prompt,
            cwd=CWD,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return fallback
    if res.returncode != 0 or not res.stdout.strip():
        return fallback

    msg = res.stdout.strip()
    msg = re.sub(r"^```[a-z]*\n|\n```$", "", msg).strip()
    subject = msg.splitlines()[0]
    if not re.match(r"^[a-z]+(\([^)]+\))?!?: .+", subject):
        return fallback
    return msg


# --- attempt-count state (drives the B -> A handoff) -------------------------


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def get_attempt_count(session_id: str, stop_hook_active: bool) -> int:
    """Attempts already spent asking Claude to self-commit this stop sequence.

    A non-continuation Stop event (stop_hook_active is False) always starts a
    fresh sequence, regardless of what's left over from an earlier turn.
    """
    if not stop_hook_active:
        return 0
    return int(load_state().get(session_id, {}).get("count", 0))


def set_attempt_count(session_id: str, count: int) -> None:
    state = load_state()
    now = time.time()
    state = {
        k: v for k, v in state.items()
        if now - v.get("ts", 0) < STATE_STALE_SECONDS
    }
    if count <= 0:
        state.pop(session_id, None)
    else:
        state[session_id] = {"count": count, "ts": now}
    save_state(state)


def request_self_commit(keep: list[str], blocked: list[str], attempt: int) -> None:
    lines = [
        "Uncommitted changes detected at end of turn. Before finishing, commit "
        "them yourself with a Conventional Commits message that reflects what "
        "actually changed:",
        "",
        "  git add -- <files>",
        '  git commit -m "<type>(<scope>): <summary>"',
        "",
        "Changed files to commit:",
    ]
    lines += [f"  - {p}" for p in keep]
    if blocked:
        lines.append("")
        lines.append("Excluded -- do NOT stage or commit these:")
        lines += [f"  - {p}" for p in blocked]
    lines.append("")
    lines.append(
        f"(auto-commit self-commit request {attempt}/{MAX_B_ATTEMPTS}; if changes "
        "are still uncommitted after this, the hook will commit them "
        "automatically with a generated message)"
    )
    print("\n".join(lines), file=sys.stderr)


# --- direct commit (方案 A fallback) ------------------------------------------


def direct_commit(payload: dict, keep: list[str]) -> bool:
    """Commit `keep` directly. Returns True on a successful commit."""
    git("reset")  # start from a clean index; we own the staging area
    add = git("add", "--", *keep)
    if add.returncode != 0:
        log(f"git add failed: {add.stderr.strip()}")
        return False

    if not git("diff", "--cached", "--quiet").returncode:
        return False  # nothing actually changed

    hits = staged_secret_hits()
    if hits:
        git("reset")
        log(f"ABORTED: possible secret in diff ({len(hits)} pattern match). "
            "Review manually, then commit yourself.")
        return False

    message = generate_message(last_user_prompt(payload.get("transcript_path")))
    sid = str(payload.get("session_id", ""))[:8]
    full = f"{message}\n\nClaude-Session: {sid}\n"

    commit = git("commit", "-m", full)
    if commit.returncode != 0:
        git("reset")
        log(f"commit failed (pre-commit hook?): {commit.stderr.strip()[:300]}")
        return False

    sha = git("rev-parse", "--short", "HEAD").stdout.strip()
    log(f"{sha} {message.splitlines()[0]}")
    return True


# --- main --------------------------------------------------------------------


def main() -> int:
    if os.environ.get(RECURSION_GUARD) == "1":
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    session_id = str(payload.get("session_id", ""))
    stop_hook_active = bool(payload.get("stop_hook_active", False))

    if not in_git_repo():
        return 0
    if mid_operation():
        log("skipped: repository is mid merge/rebase")
        return 0

    lock = CWD / ".git" / "claude-autocommit.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
        lock.unlink(missing_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        log("skipped: another auto-commit is in flight")
        return 0

    try:
        keep, blocked = collect_paths()
        if blocked:
            log("EXCLUDED sensitive path(s): " + ", ".join(blocked))
        if not keep:
            set_attempt_count(session_id, 0)
            return 0

        attempts = get_attempt_count(session_id, stop_hook_active)

        if attempts >= MAX_B_ATTEMPTS:
            log(f"self-commit request unmet after {attempts} attempt(s); "
                "falling back to direct commit")
            direct_commit(payload, keep)
            set_attempt_count(session_id, 0)
            return 0

        attempts += 1
        set_attempt_count(session_id, attempts)
        request_self_commit(keep, blocked, attempts)
        return 2
    except Exception as exc:  # never break the session
        log(f"internal error: {exc}")
        return 0
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
