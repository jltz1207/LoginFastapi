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
