# plans/ conventions

## Classify plans by status folder

Every plan lives in exactly one status folder:

| Folder | Use when |
|---|---|
| `in-progress/` | Work on the plan is actively underway |
| `partly-finished-delayed/` | Some parts are done and the rest is deferred or not yet started |
| `finished/` | Every part is done, or the document has no remaining work (e.g. a walkthrough) |

```
plans/
├── in-progress/
├── partly-finished-delayed/
└── finished/
```

- New plans start in `in-progress/`.
- When a plan's status changes, move it with `git mv` (never plain `mv`), and do not auto-commit.
- Moving a file breaks relative links: after moving, fix `../` links inside the moved file and links from other files that point to it.
  Also search `plans/`, `docs/`, `app/`, `migrations/`, `CLAUDE.md`, and `README.md` for the old path.
- Links from a plan to `docs/` go through the repo root, e.g. `../../docs/vectorstore/<file>.md`.

## File naming: date suffix

`plans/` is outside `docs/`, but follows the same date suffix rule as `docs/CLAUDE.md`:

- Every plan ends with the date it was last written or updated: `<name>-YYYYMMDD.md`.
- Updating a plan's content: rename it to today's date with `git mv`, then fix links to the old filename as above.
- Moving between status folders alone is not a content update, so it does not change the date.
