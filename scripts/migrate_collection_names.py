"""Copy unversioned Chroma collections to their EMBEDDING_VERSION-ed names.

Collection names gained an embedding version (``{NAME}`` -> ``{NAME}_v1``,
``{NAME}_{key}`` -> ``{NAME}_v1_{key}``). The app only reads the new names, so
existing data must be copied before the new code is deployed.

Vectors are copied as-is (no re-embedding), so this is only valid while the
embedding model is unchanged. Old collections are left in place; delete them by
hand once the new ones are verified.

Usage (from the repo root; dry run unless --apply):

    uv run python -m scripts.migrate_collection_names
    uv run python -m scripts.migrate_collection_names --apply
"""

import argparse
import os
import re
from dataclasses import dataclass

# Anything already carrying a version segment (v1, v2, ...) is skipped, so the
# script can be re-run and never versions a name twice.
_VERSION_SEGMENT = re.compile(r"^v\d+(?:_|$)")
DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class Rename:
    source: str
    target: str


def plan_renames(collection_names: list[str], base_name: str, version: str) -> list[Rename]:
    versioned_base = f"{base_name}_{version}"
    renames = []
    for name in sorted(collection_names):
        if name == base_name:
            renames.append(Rename(name, versioned_base))
        elif name.startswith(f"{base_name}_"):
            partition_key = name[len(base_name) + 1:]
            if not _VERSION_SEGMENT.match(partition_key):
                renames.append(Rename(name, f"{versioned_base}_{partition_key}"))
    return renames


def copy_collection(client, rename: Rename, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Copy ids, embeddings, documents and metadatas. Upserts by id, so re-running is safe."""
    # embedding_function=None matches how langchain_chroma opens collections.
    source = client.get_collection(rename.source, embedding_function=None)
    target = client.get_or_create_collection(
        rename.target, embedding_function=None, metadata=source.metadata
    )
    total = source.count()
    for offset in range(0, total, batch_size):
        batch = source.get(
            include=["embeddings", "documents", "metadatas"], limit=batch_size, offset=offset
        )
        target.upsert(
            ids=batch["ids"],
            embeddings=batch["embeddings"],
            documents=batch["documents"],
            metadatas=batch["metadatas"],
        )
    copied = target.count()
    if copied != total:
        raise RuntimeError(f"{rename.source} -> {rename.target}: expected {total} records, found {copied}")
    return copied


def migrate(client, base_name: str, version: str, apply: bool, batch_size: int = DEFAULT_BATCH_SIZE) -> list[Rename]:
    renames = plan_renames([c.name for c in client.list_collections()], base_name, version)
    for rename in renames:
        if apply:
            count = copy_collection(client, rename, batch_size)
            print(f"copied  {rename.source} -> {rename.target} ({count} records)")
        else:
            count = client.get_collection(rename.source, embedding_function=None).count()
            print(f"would copy  {rename.source} -> {rename.target} ({count} records)")
    if not renames:
        print("nothing to migrate")
    return renames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually copy (default: dry run)")
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
    print(f"chroma: {settings.CHROMA_PERSIST_DIRECTORY}  base: {settings.COLLECTION_NAME}  version: {settings.EMBEDDING_VERSION}")
    migrate(client, settings.COLLECTION_NAME, settings.EMBEDDING_VERSION, apply=args.apply, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
