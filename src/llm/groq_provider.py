import asyncio
import logging
import os
from typing import Any

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from openai import AsyncOpenAI

from src.config import LLMDefaults, settings
from src.llm.base import ChatMessage

logger = logging.getLogger(__name__)

_embed_model: Any = None
_embed_lock = asyncio.Lock()


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from fastembed import TextEmbedding

        cache_dir = settings.data_dir / "fastembed_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _embed_model = TextEmbedding(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            cache_dir=str(cache_dir),
        )
    return _embed_model


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        self._available_models: list[str] | None = None

    async def validate_key(self) -> bool:
        try:
            res = await self._client.models.list()
            chat_models = [
                m.id for m in res.data
                if "whisper" not in m.id.lower()
                and "guard" not in m.id.lower()
                and "orpheus" not in m.id.lower()
            ]
            self._available_models = chat_models or [m.id for m in res.data]
            return True
        except Exception:
            logger.exception("Groq key validation failed")
            return False

    async def _get_models(self) -> list[str]:
        if self._available_models is None:
            try:
                res = await self._client.models.list()
                chat_models = [
                    m.id for m in res.data
                    if "whisper" not in m.id.lower()
                    and "guard" not in m.id.lower()
                    and "orpheus" not in m.id.lower()
                ]
                self._available_models = chat_models or [m.id for m in res.data]
            except Exception:
                self._available_models = []
        return self._available_models

    async def _resolve_model(self, heavy: bool = False) -> str:
        preferred = LLMDefaults.GROQ_CHAT_HEAVY if heavy else LLMDefaults.GROQ_CHAT_LIGHT
        available = await self._get_models()
        if preferred in available:
            return preferred

        light_order = [
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "allam-2-7b",
            "llama-3.1-8b-instant",
        ]
        heavy_order = [
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "llama-3.3-70b-versatile",
        ]
        order = heavy_order if heavy else light_order
        for candidate in order:
            if candidate in available:
                return candidate

        return available[0] if available else preferred

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        model = await self._resolve_model(heavy=heavy)
        formatted = [{"role": m.role, "content": m.content} for m in messages]
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=formatted,
                temperature=0.7,
                max_tokens=250,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            err_str = str(e).lower()
            if "model_not_found" in err_str or "does not exist" in err_str:
                logger.warning("Groq model %s not found, checking alternatives...", model)
                self._available_models = None
                fallback_model = await self._resolve_model(heavy=not heavy)
                if fallback_model != model:
                    logger.info("Retrying chat with fallback model: %s", fallback_model)
                    resp = await self._client.chat.completions.create(
                        model=fallback_model,
                        messages=formatted,
                        temperature=0.7,
                        max_tokens=250,
                    )
                    return resp.choices[0].message.content or ""
            raise

    async def embed(self, text: str) -> list[float]:
        def _sync_embed(t: str) -> list[float]:
            model = _get_embed_model()
            embeddings = list(model.embed([t]))
            if embeddings:
                return embeddings[0].tolist()
            return []

        return await asyncio.to_thread(_sync_embed, text)
