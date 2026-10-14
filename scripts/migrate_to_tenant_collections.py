"""Move Chroma records into per-tenant collections and backfill `tenant_id` metadata.

Two gaps left over from the switch to PARTITION_METHOD=3 (one collection per tenant):

1. Chunks ingested before `tenant_id` was added to their metadata are invisible:
   `StoreIndexer.scope_filter()` now requires `{"tenant_id": ...}` on every query.
2. Data still sits in per-user (`{NAME}_{VER}_{user_id}`) or global (`{NAME}_{VER}`)
   collections, while the app now only reads `{NAME}_{VER}_{tenant_id}`.

Every record in every collection of the current EMBEDDING_VERSION is assigned an owner
(user_id, tenant_id) from the DB, which is the source of truth (existing values in
metadata are overwritten):

- by its `knowledge_base_id` and the `AsistantKnowledgeBases` table, which also fills
  in `user_id` for early chunks that were ingested without one;
- otherwise by its `user_id` and the `users` table.

It is then upserted, with `user_id` and `tenant_id` set, into that tenant's collection.
Records already in the right collection are updated in place, so the script can be
re-run safely. Records that cannot be resolved are skipped and reported, and so is a
record whose `user_id` differs from its KB's owner: handing it to the owner would
expose it to a user who never uploaded it.

Vectors are copied as-is (no re-embedding). Source collections are left in place;
delete them by hand once the new ones are verified. Run the Alembic migration that adds
`tenant_id` first, and restart the app afterwards so the BM25 cache is rebuilt.
Collections without an EMBEDDING_VERSION segment (`{NAME}_{user_id}`) are not read:
run `scripts.migrate_collection_names --apply` before this script.

Usage (from the repo root; dry run unless --apply):

    uv run python -m scripts.migrate_to_tenant_collections
    uv run python -m scripts.migrate_to_tenant_collections --apply
"""

import argparse
import os
from collections import Counter, defaultdict

DEFAULT_BATCH_SIZE = 500


def source_collections(collection_names: list[str], base_name: str, version: str) -> list[str]:
    """Every collection of this embedding version: the global one and all partitioned ones."""
    versioned_base = f"{base_name}_{version}"
    return sorted(
        name for name in collection_names if name == versioned_base or name.startswith(f"{versioned_base}_")
    )


def resolve_owner(
    metadata: dict, user_to_tenant: dict[str, str], kb_owners: dict[str, tuple[str, str]]
) -> tuple[str, str] | None:
    """(user_id, tenant_id) for a record, or None when it must be skipped."""
    record_user = metadata.get("user_id")
    owner = kb_owners.get(metadata.get("knowledge_base_id"))
    if owner is not None:
        return owner if record_user in (None, owner[0]) else None
    if record_user in user_to_tenant:
        return record_user, user_to_tenant[record_user]
    return None


def migrate(
    client,
    base_name: str,
    version: str,
    user_to_tenant: dict[str, str],
    apply: bool,
    batch_size: int = DEFAULT_BATCH_SIZE,
    kb_owners: dict[str, tuple[str, str]] | None = None,
) -> tuple[Counter, Counter]:
    """Returns (moved, skipped): moved[(source, target)] and skipped[source] record counts."""
    versioned_base = f"{base_name}_{version}"
    kb_owners = kb_owners or {}
    moved: Counter = Counter()
    skipped: Counter = Counter()
    expected_ids: dict[str, set[str]] = defaultdict(set)

    for source_name in source_collections([c.name for c in client.list_collections()], base_name, version):
        # embedding_function=None matches how langchain_chroma opens collections.
        source = client.get_collection(source_name, embedding_function=None)
        total = source.count()
        # Upserting into the source itself (already the right tenant collection) only
        # rewrites metadata of existing ids, so offset paging stays stable.
        for offset in range(0, total, batch_size):
            batch = source.get(include=["embeddings", "documents", "metadatas"], limit=batch_size, offset=offset)
            by_target: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
            for record_id, embedding, document, metadata in zip(
                batch["ids"], batch["embeddings"], batch["documents"], batch["metadatas"]
            ):
                metadata = metadata or {}
                owner = resolve_owner(metadata, user_to_tenant, kb_owners)
                if owner is None:
                    skipped[source_name] += 1
                    continue
                user_id, tenant_id = owner
                target_name = f"{versioned_base}_{tenant_id}"
                group = by_target[target_name]
                group["ids"].append(record_id)
                group["embeddings"].append(embedding)
                group["documents"].append(document)
                group["metadatas"].append({**metadata, "user_id": user_id, "tenant_id": tenant_id})
                moved[(source_name, target_name)] += 1
                expected_ids[target_name].add(record_id)

            if apply:
                for target_name, group in by_target.items():
                    target = client.get_or_create_collection(
                        target_name, embedding_function=None, metadata=source.metadata
                    )
                    target.upsert(**group)

    if apply:
        for target_name, ids in expected_ids.items():
            target = client.get_collection(target_name, embedding_function=None)
            found = len(target.get(ids=list(ids), include=[])["ids"])
            if found != len(ids):
                raise RuntimeError(f"{target_name}: expected {len(ids)} records, found {found}")

    verb = "moved" if apply else "would move"
    for (source_name, target_name), count in sorted(moved.items()):
        arrow = "(in place)" if source_name == target_name else f"-> {target_name}"
        print(f"{verb}  {source_name} {arrow} ({count} records)")
    for source_name, count in sorted(skipped.items()):
        print(f"SKIPPED {source_name}: {count} records with no resolvable owner, or a user_id that differs from the KB owner")
    if not moved and not skipped:
        print("nothing to migrate")
    return moved, skipped


def load_ownership(database_url: str) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Returns (user_to_tenant, kb_owners) from the DB."""
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            users = conn.execute(text("SELECT id, tenant_id FROM users")).all()
            kbs = conn.execute(text('SELECT id, user_id, tenant_id FROM "AsistantKnowledgeBases"')).all()
    finally:
        engine.dispose()
    # Chroma metadata stores ids as str (see docs/id-type-convention-20260827.md).
    user_to_tenant = {str(user_id): str(tenant_id) for user_id, tenant_id in users}
    kb_owners = {str(kb_id): (str(user_id), str(tenant_id)) for kb_id, user_id, tenant_id in kbs}
    return user_to_tenant, kb_owners


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    # Same env loading as app/main.py, before settings is imported.
    from dotenv import load_dotenv

    load_dotenv(f".env.{os.getenv('APP_ENV', 'dev')}")

    import chromadb

    from app.core import settings

    # PersistentClient creates the directory if it is missing, which would turn a wrong
    # path (e.g. running from the wrong cwd) into a silent "nothing to migrate".
    if not os.path.isdir(settings.CHROMA_PERSIST_DIRECTORY):
        raise SystemExit(f"Chroma directory not found: {os.path.abspath(settings.CHROMA_PERSIST_DIRECTORY)}")
    client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIRECTORY)
    user_to_tenant, kb_owners = load_ownership(settings.DATABASE_URL)
    print(
        f"chroma: {settings.CHROMA_PERSIST_DIRECTORY}  base: {settings.COLLECTION_NAME}  "
        f"version: {settings.EMBEDDING_VERSION}  users: {len(user_to_tenant)}  kbs: {len(kb_owners)}"
    )
    migrate(
        client,
        settings.COLLECTION_NAME,
        settings.EMBEDDING_VERSION,
        user_to_tenant,
        apply=args.apply,
        batch_size=args.batch_size,
        kb_owners=kb_owners,
    )


if __name__ == "__main__":
    main()
