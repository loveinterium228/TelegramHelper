"""Embedded Qdrant vector database store for message embeddings and semantic search.

Dynamically provisions the collection upon first vector insertion with matching dimensions,
allowing seamless provider and embedding model changes without manual migrations.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from src.config import settings


logger = logging.getLogger(__name__)

COLLECTION = "messages"


@dataclass
class VectorHit:
    """Represents a scored semantic search result."""
    user_id: int
    peer_id: int
    peer_name: str | None
    message_id: int
    text: str
    date_iso: str | None
    score: float


class VectorStore:
    """Vector database manager wrapping an embedded or in-memory Qdrant client."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = path
        self._client: QdrantClient | None = None
        self._lock = asyncio.Lock()
        self._dim: int | None = None

    def _get_client(self) -> QdrantClient:
        """Lazily initialize the Qdrant client to avoid locking during module import."""
        if self._client is None:
            if self._path == ":memory:":
                self._client = QdrantClient(":memory:")
            else:
                db_path = Path(self._path) if self._path else (settings.data_dir / "qdrant")
                db_path.mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=str(db_path))
        return self._client

    async def _ensure_collection(self, dim: int) -> None:
        if self._dim == dim:
            return
        async with self._lock:
            if self._dim == dim:
                return

            def _check_or_create() -> None:
                client = self._get_client()
                existing = {c.name for c in client.get_collections().collections}
                if COLLECTION in existing:
                    info = client.get_collection(COLLECTION)
                    actual = info.config.params.vectors.size
                    if actual != dim:
                        logger.warning(
                            "Recreating Qdrant collection %s: dimension mismatch (%d -> %d)",
                            COLLECTION, actual, dim,
                        )
                        client.delete_collection(COLLECTION)
                        client.create_collection(
                            COLLECTION,
                            vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
                        )
                else:
                    client.create_collection(
                        COLLECTION,
                        vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
                    )

            await asyncio.to_thread(_check_or_create)
            self._dim = dim

    @staticmethod
    def _point_id(user_id: int, peer_id: int, message_id: int) -> str:
        """Deterministic UUID generation to prevent collisions with 64-bit Telegram IDs."""
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}:{peer_id}:{message_id}"))

    async def upsert(
        self,
        *,
        user_id: int,
        peer_id: int,
        peer_name: str | None,
        message_id: int,
        text: str,
        date_iso: str | None,
        embedding: list[float],
    ) -> None:
        """Upsert a message vector and metadata payload."""
        await self._ensure_collection(len(embedding))

        def _do() -> None:
            client = self._get_client()
            client.upsert(
                collection_name=COLLECTION,
                points=[
                    qmodels.PointStruct(
                        id=self._point_id(user_id, peer_id, message_id),
                        vector=embedding,
                        payload={
                            "user_id": user_id,
                            "peer_id": peer_id,
                            "peer_name": peer_name,
                            "message_id": message_id,
                            "text": text,
                            "date_iso": date_iso,
                        },
                    )
                ],
            )

        await asyncio.to_thread(_do)

    async def search(
        self,
        *,
        user_id: int,
        embedding: list[float],
        limit: int = 10,
        peer_id: int | None = None,
    ) -> list[VectorHit]:
        """Perform semantic similarity search filtered by user_id and optional peer_id."""
        if self._dim is None:
            return []
        flt = qmodels.Filter(
            must=[qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id))]
        )
        if peer_id is not None:
            flt.must.append(
                qmodels.FieldCondition(key="peer_id", match=qmodels.MatchValue(value=peer_id))
            )

        def _do() -> list[qmodels.ScoredPoint]:
            client = self._get_client()
            if hasattr(client, "query_points"):
                response = client.query_points(
                    collection_name=COLLECTION,
                    query=embedding,
                    limit=limit,
                    query_filter=flt,
                )
                return response.points
            if hasattr(client, "search"):
                return client.search(
                    collection_name=COLLECTION,
                    query_vector=embedding,
                    limit=limit,
                    query_filter=flt,
                )
            return []

        raw = await asyncio.to_thread(_do)
        return [
            VectorHit(
                user_id=p.payload.get("user_id"),
                peer_id=p.payload.get("peer_id"),
                peer_name=p.payload.get("peer_name"),
                message_id=p.payload.get("message_id"),
                text=p.payload.get("text", ""),
                date_iso=p.payload.get("date_iso"),
                score=float(p.score),
            )
            for p in raw
        ]


vector_store = VectorStore()
