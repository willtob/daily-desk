"""OpenAI embeddings with an on-disk cache.

Phase 3 embeds two kinds of text: the interest-profile references (embedded once,
then stable across runs) and article title+summary (mostly the same articles from
one run to the next). Both go through the same cache, so re-running the score
node while tuning the profile costs almost nothing after the first call — which
matters, because tuning means running it a lot.

Vectors are L2-normalized on the way out so cosine similarity is a plain dot
product.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"

# .cache/ lives at the repo root: src/esp_news/embeddings.py -> parents[2].
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "embeddings.json"

# The embeddings endpoint takes a list; 128 keeps each request small.
_BATCH_SIZE = 128
# text-embedding-3-small caps at 8191 tokens. Nothing we send is close, but a
# feed that ships full article text in its summary shouldn't blow up a request.
_MAX_CHARS = 8000


class MissingAPIKeyError(RuntimeError):
    """Raised when an embedding call is attempted without OPENAI_API_KEY set."""


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


class EmbeddingClient:
    """Embeds text with OpenAI, backed by a JSON cache on disk."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        cache_path: str | Path | None = DEFAULT_CACHE_PATH,
    ) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache = self._load_cache()
        self._client: OpenAI | None = None
        # Counters the CLI reports, so API usage is visible rather than implied.
        self.api_calls = 0
        self.texts_embedded = 0
        self.cache_hits = 0

    # ── cache ────────────────────────────────────────────────────────────────

    def _load_cache(self) -> dict[str, list[float]]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Ignoring unreadable embedding cache %s: %s", self.cache_path, exc
            )
            return {}

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so an interrupted run can't leave a truncated cache.
        tmp = self.cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._cache))
        tmp.replace(self.cache_path)

    def _key(self, text: str) -> str:
        """Cache key over model + text, so switching models can't reuse vectors."""
        return hashlib.sha256(f"{self.model}\x00{text}".encode()).hexdigest()

    # ── embedding ────────────────────────────────────────────────────────────

    def _openai(self) -> OpenAI:
        if self._client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise MissingAPIKeyError(
                    "OPENAI_API_KEY is not set — add it to .env (see .env.example)."
                )
            self._client = OpenAI()
        return self._client

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an ``(len(texts), dim)`` matrix of normalized embeddings.

        Only texts missing from the cache hit the API, deduplicated first so a
        repeated string is paid for once.
        """
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)

        prepared = [t[:_MAX_CHARS] for t in texts]
        unique = list(dict.fromkeys(prepared))  # dedupe, preserving order
        missing = [t for t in unique if self._key(t) not in self._cache]
        self.cache_hits += len(unique) - len(missing)

        for start in range(0, len(missing), _BATCH_SIZE):
            batch = missing[start : start + _BATCH_SIZE]
            response = self._openai().embeddings.create(model=self.model, input=batch)
            self.api_calls += 1
            self.texts_embedded += len(batch)
            for text, item in zip(batch, response.data):
                self._cache[self._key(text)] = item.embedding

        if missing:
            self._save_cache()
            logger.info(
                "Embedded %d new texts, %d already cached (%d API call(s))",
                len(missing),
                len(unique) - len(missing),
                self.api_calls,
            )
        else:
            logger.info("All %d texts served from the embedding cache", len(unique))

        vectors = np.array(
            [self._cache[self._key(t)] for t in prepared], dtype=np.float32
        )
        return _l2_normalize(vectors)
