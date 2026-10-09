# docs/plans/ conventions

## Classify plans by status folder

Every plan lives in exactly one status folder:

| Folder | Use when |
|---|---|
| `in-progress/` | Work on the plan is actively underway |
| `partly-finished-delayed/` | Some parts are done and the rest is deferred or not yet started |
| `finished/` | Every part is done, or the document has no remaining work (e.g. a walkthrough) |

```
docs/plans/
├── in-progress/
├── partly-finished-delayed/
└── finished/
```

- New plans start in `in-progress/`.
- When a plan's status changes, move it with `git mv` (never plain `mv`), and do not auto-commit.
- Moving a file one level deeper breaks its relative links: after moving, fix `../` links inside the moved file and links from other files that point to it.
  Also search `docs/`, `app/`, `CLAUDE.md`, and `README.md` for the old path.
- The date suffix rule in `docs/CLAUDE.md` still applies. Moving between folders alone is not a content update, so it does not change the date.
