# plans/ conventions

## What belongs here

`plans/` holds documents that track work: implementation plans and code reviews with fix items.

- Walkthroughs, explanations, and learning notes go in `docs/`, even when a plan produced them.
- Session handoffs do not belong here. Put a still-relevant decision in the plan it affects.

## Classify plans by status folder

Every plan lives in exactly one status folder:

| Folder | Use when |
|---|---|
| `backlog/` | Written, but no work has started |
| `in-progress/` | Work on the plan is actively underway |
| `deferred/` | Some parts are done and the rest is paused |
| `finished/` | Every part is done |

```
plans/
├── backlog/
├── in-progress/
├── deferred/
└── finished/
```

- New plans start in `in-progress/`, or in `backlog/` if no work will start yet.
- When a plan's status changes, move it with `git mv` (never plain `mv`), and do not auto-commit.
- Moving a file breaks relative links: after moving, fix `../` links inside the moved file and links from other files that point to it.
  Also search `plans/`, `docs/`, `app/`, `migrations/`, `CLAUDE.md`, and `README.md` for the old path.
- Links from a plan to `docs/` go through the repo root, e.g. `../../docs/vectorstore/<file>.md`.

## File naming

- Code reviews start with `review-`, e.g. `review-meta-branch-20261015.md`. Other plans have no prefix.
- Use `-` between words, never `_`.

### Date suffix

`plans/` is outside `docs/`, but follows the same date suffix rule as `docs/CLAUDE.md`:

- Every plan ends with the date it was last written or updated: `<name>-YYYYMMDD.md`.
- Updating a plan's content: rename it to today's date with `git mv`, then fix links to the old filename as above.
- Moving between status folders alone is not a content update, so it does not change the date.
- Changing only the provenance table (below) is not a content update either.

## Record provenance in a table

Every plan has a provenance table right below its `#` title, so you can trace who wrote it, when, where the work happened, and which Claude sessions hold the discussion behind it. It is the only metadata block under the title: do not repeat these fields in a `>` line.

```markdown
# <plan title>

| | |
|---|---|
| Author | `Jason` |
| Created | 2026-10-14 |
| Branches | `worktree-feature-add-workers` |
| Base commit | `a73f407` |
| Sessions | `87e29356-dcaa-4caf-b2ab-a5fa2305fe52` |
```

| Field | Meaning |
|---|---|
| Author | The git author name exactly as git records it, in backticks — never typed by hand or taken from `git config user.name`. For an existing plan, it is the author of the commit that first added the file (`git log --follow --diff-filter=A --format=%an -- <file> \| tail -1`; follow it back through earlier paths such as `docs/plans/`). For a new plan, it is the name part of `git var GIT_AUTHOR_IDENT`, which includes git's `HOSTNAME\user` fallback (e.g. `LAPTOP-I592N3QF\user`) on a machine without `user.name`. Claude's involvement is recorded by Sessions, not here. |
| Created | The date the plan was first written, `YYYY-MM-DD`. For a code review, the review date. It never changes; the filename's date suffix is the last-updated date. |
| Branches | The branch the plan was created on, first. Append a branch when work on the plan moves to another one (e.g. a new worktree). |
| Base commit | Short SHA of `HEAD` when the plan was created. Worktree branches are deleted after merge, so this is the anchor that outlives the branch name. |
| Sessions | Claude Code session IDs that created or substantially changed the plan, oldest first. Resume one with `claude --resume <id>`. |

- Put multiple values in one cell, separated by `<br>`, oldest first.
- Write `—` when a value is unknown or empty. Never guess.
- Do not add a Status row: the status folder is the only source of truth for status.
- When creating a plan, fill the rows from `git var GIT_AUTHOR_IDENT`, `git branch --show-current`, `git rev-parse --short HEAD`, and `echo $CLAUDE_CODE_SESSION_ID`.
- When a later session makes more than a typo fix, append its session ID (and its branch, if new).
- Session transcripts are local to one machine and are deleted after `cleanupPeriodDays` (default 30 days), so a session ID may stop resolving. Record decisions in the plan itself; the session ID is only a pointer to extra context.
