# docs/plans/ conventions

## Marking a finished plan

When every phase of a plan is finished, add a `[Done]` prefix to its file name:

```
[Done]<name>-YYYYMMDD.md
```

Example: `vectorstore-structure-fixes-20261009.md` → `[Done]vectorstore-structure-fixes-20261009.md`

- Rename with `git mv`, and do not auto-commit.
- The date suffix rule in `docs/CLAUDE.md` still applies.
- After renaming, search `docs/`, `app/`, `CLAUDE.md`, and `README.md` for links to the old filename and update them.
