"""Tests for Qdrant vector store in-memory operations and semantic search."""
import pytest

from src.core.vector_store import VectorStore


@pytest.mark.asyncio
async def test_vector_store_in_memory_upsert_and_search():
    store = VectorStore(path=":memory:")

    # Vector dimensions: 4
    vec_apple = [1.0, 0.0, 0.0, 0.0]
    vec_pear = [0.95, 0.05, 0.0, 0.0]
    vec_car = [0.0, 0.0, 1.0, 0.0]

    user_id = 42

    await store.upsert(
        user_id=user_id,
        peer_id=101,
        peer_name="Fruit Channel",
        message_id=1,
        text="Apples are delicious and healthy fruits.",
        date_iso="2026-05-10T12:00:00",
        embedding=vec_apple,
    )
    await store.upsert(
        user_id=user_id,
        peer_id=101,
        peer_name="Fruit Channel",
        message_id=2,
        text="Pears have a sweet flavor.",
        date_iso="2026-05-10T12:05:00",
        embedding=vec_pear,
    )
    await store.upsert(
        user_id=user_id,
        peer_id=102,
        peer_name="Auto Group",
        message_id=10,
        text="Electric vehicles are becoming more common.",
        date_iso="2026-05-10T12:10:00",
        embedding=vec_car,
    )

    # Search for fruits with query vector close to apple
    hits = await store.search(
        user_id=user_id,
        embedding=[0.98, 0.02, 0.0, 0.0],
        limit=5,
    )
    assert len(hits) == 3
    # Top result should be apple or pear with high cosine similarity score
    assert hits[0].peer_name == "Fruit Channel"
    assert hits[0].score > 0.9

    # Filter by peer_id=102 (Auto Group)
    auto_hits = await store.search(
        user_id=user_id,
        embedding=[0.0, 0.0, 0.99, 0.0],
        limit=5,
        peer_id=102,
    )
    assert len(auto_hits) == 1
    assert auto_hits[0].message_id == 10
    assert "Electric vehicles" in auto_hits[0].text
