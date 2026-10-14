import uuid

import chromadb
import pytest

from scripts.migrate_to_tenant_collections import migrate, source_collections

USERS = {"u1": "t1", "u2": "t1", "u3": "t2"}


def test_sources_are_every_collection_of_the_current_version():
    names = ["kb", "kb_u1", "kb_v1", "kb_v1_u1", "kb_v1_t1", "kb_v2_u1", "other_v1_u1"]

    assert source_collections(names, "kb", "v1") == ["kb_v1", "kb_v1_t1", "kb_v1_u1"]


@pytest.fixture
def client_and_base():
    base = f"kb{uuid.uuid4().hex[:8]}"  # EphemeralClient state is shared within the process
    return chromadb.EphemeralClient(), base


def _seed(client, name: str, user_ids: list[str | None], with_tenant: str | None = None):
    collection = client.get_or_create_collection(name, embedding_function=None)
    metadatas = []
    for user_id in user_ids:
        metadata = {"knowledge_base_id": "kb-1"}
        if user_id is not None:
            metadata["user_id"] = user_id
        if with_tenant is not None:
            metadata["tenant_id"] = with_tenant
        metadatas.append(metadata)
    collection.add(
        ids=[f"{name}-{i}" for i in range(len(user_ids))],
        embeddings=[[float(i), 1.0, 0.0] for i in range(len(user_ids))],
        documents=[f"doc {i}" for i in range(len(user_ids))],
        metadatas=metadatas,
    )


def _records(client, name: str) -> dict[str, dict]:
    raw = client.get_collection(name, embedding_function=None).get(include=["metadatas"])
    return dict(zip(raw["ids"], raw["metadatas"]))


def test_per_user_and_global_records_land_in_their_tenant_collection(client_and_base):
    client, base = client_and_base
    _seed(client, f"{base}_v1_u1", ["u1", "u1"])
    _seed(client, f"{base}_v1_u3", ["u3"])
    _seed(client, f"{base}_v1", ["u2", "u3"])

    migrate(client, base, "v1", USERS, apply=True, batch_size=1)

    t1 = _records(client, f"{base}_v1_t1")
    t2 = _records(client, f"{base}_v1_t2")
    assert sorted(t1) == sorted([f"{base}_v1_u1-0", f"{base}_v1_u1-1", f"{base}_v1-0"])
    assert sorted(t2) == sorted([f"{base}_v1_u3-0", f"{base}_v1-1"])
    assert {m["tenant_id"] for m in t1.values()} == {"t1"}
    assert {m["tenant_id"] for m in t2.values()} == {"t2"}
    # source collections are kept
    assert _records(client, f"{base}_v1_u1")


def test_tenant_collection_is_backfilled_in_place_and_db_wins(client_and_base):
    client, base = client_and_base
    _seed(client, f"{base}_v1_t1", ["u1", "u2"])
    _seed(client, f"{base}_v1_t2", ["u3"], with_tenant="stale")

    moved, _ = migrate(client, base, "v1", USERS, apply=True)

    assert moved[(f"{base}_v1_t1", f"{base}_v1_t1")] == 2
    assert {m["tenant_id"] for m in _records(client, f"{base}_v1_t1").values()} == {"t1"}
    assert {m["tenant_id"] for m in _records(client, f"{base}_v1_t2").values()} == {"t2"}


def test_unknown_or_missing_user_is_skipped(client_and_base):
    client, base = client_and_base
    _seed(client, f"{base}_v1_u1", ["u1", "ghost", None])

    moved, skipped = migrate(client, base, "v1", USERS, apply=True)

    assert skipped[f"{base}_v1_u1"] == 2
    assert list(_records(client, f"{base}_v1_t1")) == [f"{base}_v1_u1-0"]


def test_dry_run_writes_nothing(client_and_base):
    client, base = client_and_base
    _seed(client, f"{base}_v1_u1", ["u1"])

    moved, _ = migrate(client, base, "v1", USERS, apply=False)

    assert moved[(f"{base}_v1_u1", f"{base}_v1_t1")] == 1
    assert f"{base}_v1_t1" not in [c.name for c in client.list_collections()]
    assert "tenant_id" not in _records(client, f"{base}_v1_u1")[f"{base}_v1_u1-0"]


def test_rerun_is_idempotent(client_and_base):
    client, base = client_and_base
    _seed(client, f"{base}_v1_u1", ["u1", "u2"])

    migrate(client, base, "v1", USERS, apply=True)
    migrate(client, base, "v1", USERS, apply=True)

    assert client.get_collection(f"{base}_v1_t1", embedding_function=None).count() == 2
