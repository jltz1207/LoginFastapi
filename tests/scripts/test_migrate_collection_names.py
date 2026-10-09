import uuid

import chromadb
import pytest

from scripts.migrate_collection_names import Rename, copy_collection, migrate, plan_renames


def test_plan_covers_every_partition_strategy():
    names = ["kb", "kb_user-1", "kb_tenant-1", "other_collection"]

    assert plan_renames(names, "kb", "v1") == [
        Rename("kb", "kb_v1"),
        Rename("kb_tenant-1", "kb_v1_tenant-1"),
        Rename("kb_user-1", "kb_v1_user-1"),
    ]


def test_plan_skips_already_versioned_names():
    names = ["kb_v1", "kb_v1_user-1", "kb_v2_user-1"]

    assert plan_renames(names, "kb", "v1") == []


@pytest.fixture
def client_and_base():
    base = f"kb{uuid.uuid4().hex[:8]}"  # EphemeralClient state is shared within the process
    return chromadb.EphemeralClient(), base


def _seed(client, name: str, n: int):
    collection = client.get_or_create_collection(name, embedding_function=None)
    collection.add(
        ids=[f"id-{i}" for i in range(n)],
        embeddings=[[float(i), 1.0, 0.0] for i in range(n)],
        documents=[f"doc {i}" for i in range(n)],
        metadatas=[{"tenant_id": "t", "user_id": "u", "knowledge_base_id": str(i % 2)} for i in range(n)],
    )


def test_copy_preserves_ids_vectors_documents_and_metadata(client_and_base):
    client, base = client_and_base
    _seed(client, f"{base}_u1", 7)

    copied = copy_collection(client, Rename(f"{base}_u1", f"{base}_v1_u1"), batch_size=3)

    source = client.get_collection(f"{base}_u1", embedding_function=None).get(include=["embeddings", "documents", "metadatas"])
    target = client.get_collection(f"{base}_v1_u1", embedding_function=None).get(include=["embeddings", "documents", "metadatas"])
    assert copied == 7
    assert sorted(target["ids"]) == sorted(source["ids"])
    by_id = lambda r: {i: (list(e), d, m) for i, e, d, m in zip(r["ids"], r["embeddings"], r["documents"], r["metadatas"])}
    assert by_id(target) == by_id(source)


def test_dry_run_writes_nothing(client_and_base):
    client, base = client_and_base
    _seed(client, f"{base}_u1", 2)

    migrate(client, base, "v1", apply=False)

    assert f"{base}_v1_u1" not in [c.name for c in client.list_collections()]


def test_rerun_is_idempotent_and_keeps_the_source(client_and_base):
    client, base = client_and_base
    _seed(client, f"{base}_u1", 4)

    migrate(client, base, "v1", apply=True)
    migrate(client, base, "v1", apply=True)

    names = [c.name for c in client.list_collections()]
    assert f"{base}_u1" in names
    assert client.get_collection(f"{base}_v1_u1", embedding_function=None).count() == 4
