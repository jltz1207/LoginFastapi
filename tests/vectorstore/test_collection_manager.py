import uuid

import chromadb
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from app.core import settings
from app.vectorstore.collection_manager import Collection_manager, vector_partition_strategy


@pytest.fixture(autouse=True)
def _unique_collection_prefix(monkeypatch):
    # EphemeralClient instances share one in-memory system, so isolate tests by name.
    monkeypatch.setattr(settings, "COLLECTION_NAME", f"test_{uuid.uuid4().hex[:8]}")


def _manager(strategy: vector_partition_strategy, max_collections: int = 256) -> Collection_manager:
    return Collection_manager(
        client=chromadb.EphemeralClient(),
        embedding_function=DeterministicFakeEmbedding(size=8),
        partition_strategy=strategy,
        max_collections=max_collections,
    )


def test_per_tenant_users_share_one_handle():
    manager = _manager(vector_partition_strategy.ONE_TENANT_ONE_COLLECTION)

    first = manager.get_or_create_collection(user_id="u1", tenant_id="t1")
    second = manager.get_or_create_collection(user_id="u2", tenant_id="t1")

    assert first is second
    assert first._collection.name == f"{settings.COLLECTION_NAME}_t1"


def test_global_collection_is_one_handle_for_everyone():
    manager = _manager(vector_partition_strategy.GLOBAL_COLLECTION)

    first = manager.get_or_create_collection(user_id="u1", tenant_id="t1")
    second = manager.get_or_create_collection(user_id="u2", tenant_id="t2")

    assert first is second
    assert first._collection.name == settings.COLLECTION_NAME


def test_per_user_gives_each_user_its_own_handle():
    manager = _manager(vector_partition_strategy.ONE_USER_ONE_COLLECTION)

    first = manager.get_or_create_collection(user_id="u1", tenant_id="t1")
    second = manager.get_or_create_collection(user_id="u2", tenant_id="t1")

    assert first is not second
    assert first._collection.name == f"{settings.COLLECTION_NAME}_u1"
    assert manager.get_or_create_collection(user_id="u1", tenant_id="t1") is first


def test_least_recently_used_handle_is_evicted_beyond_the_bound():
    manager = _manager(vector_partition_strategy.ONE_USER_ONE_COLLECTION, max_collections=2)

    u1 = manager.get_or_create_collection(user_id="u1", tenant_id="t")
    u2 = manager.get_or_create_collection(user_id="u2", tenant_id="t")
    manager.get_or_create_collection(user_id="u1", tenant_id="t")  # u1 is now most recent
    manager.get_or_create_collection(user_id="u3", tenant_id="t")  # evicts u2

    assert manager.get_or_create_collection(user_id="u1", tenant_id="t") is u1
    assert manager.get_or_create_collection(user_id="u2", tenant_id="t") is not u2


def test_evict_forces_a_fresh_handle():
    manager = _manager(vector_partition_strategy.ONE_USER_ONE_COLLECTION)
    first = manager.get_or_create_collection(user_id="u1", tenant_id="t1")

    manager.evict(user_id="u1", tenant_id="t1")

    assert manager.get_or_create_collection(user_id="u1", tenant_id="t1") is not first
