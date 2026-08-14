"""OpenAI embeddings with a SQLite cache.

Phase 3 embeds two kinds of text: the interest-profile references (embedded once,
then stable across runs) and article title+summary (mostly the same articles from
one run to the next). Both go through the same cache, so re-running the score
node while tuning the profile costs almost nothing after the first call — which
matters, because tuning means running it a lot.

This was a single JSON object and it did not survive contact with a few months
of runs: 2689 entries, 83MB, growing ~7.5MB a run. JSON stores a 1536-dim vector
as 1536 float64 reprs, about 31KB each, and the whole file was parsed on every
client construction and rewritten in full whenever one new text was added. The
live working set is a few hundred vectors — articles inside the lookback window
plus the profile references — so nearly all of that work was for vectors nothing
was going to ask for.

Two changes fix it. Vectors are stored as float32 blobs (~6KB), which is not a
precision loss: the old code parsed float64 and immediately narrowed to float32
to build the matrix, so the numbers reaching the caller are the same ones.
And lookups ask only for the hashes the current call needs. There is deliberately
no "load the cache" path anywhere in this module — adding one back would restore
the original problem in a new format.

Eviction is on ``last_used``, never on age since creation, and that distinction
is load-bearing. The profile references are written once and read by every run;
evicting on creation date would throw them out weekly and re-buy them. The same
goes for the like/dislike reference vectors that are planned to join them. The
sweep runs after a call's lookups rather than before, so anything the call
touched has already been marked as used and cannot be collected by the very run
that needed it.

Concurrency: one connection per client, shared across threads and guarded by a
lock, so a future threaded caller is safe without reopening the database per
call — today ``score.py`` only calls ``embed`` from the main thread. Separate
processes each get their own connection, which is what WAL mode is for. Writes
are ordinary SQLite transactions, so an interrupted run leaves the database
whole, as the previous write-then-rename did for the JSON file.

Vectors are L2-normalized on the way out so cosine similarity is a plain dot
product.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"

# .cache/ lives at the repo root: src/esp_news/clients/embeddings.py -> parents[3].
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[3] / ".cache" / "embeddings.db"

# How long an unused vector is kept. A week spans any normal gap between runs,
# so in practice only articles that fell out of the lookback window expire.
EVICT_AFTER_DAYS = 7

# The embeddings endpoint takes a list; 128 keeps each request small.
_BATCH_SIZE = 128
# text-embedding-3-small caps at 8191 tokens. Nothing we send is close, but a
# feed that ships full article text in its summary shouldn't blow up a request.
_MAX_CHARS = 8000
# SQLite's default ceiling on host parameters is 999; lookups are chunked to
# stay under it regardless of how many articles a run scores.
_MAX_SQL_VARS = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    key       TEXT PRIMARY KEY,
    model     TEXT NOT NULL,
    vector    BLOB NOT NULL,
    last_used REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS embeddings_last_used ON embeddings (last_used);
"""


class MissingAPIKeyError(RuntimeError):
    """Raised when an embedding call is attempted without OPENAI_API_KEY set."""


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


def _chunked(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class EmbeddingClient:
    """Embeds text with OpenAI, backed by a SQLite cache on disk."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        cache_path: str | Path | None = DEFAULT_CACHE_PATH,
    ) -> None:
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self._lock = threading.Lock()
        self._conn = self._connect()
        # Holds this client's vectors when there is no database to hold them,
        # so ``--no-cache`` still pays for a repeated text only once per client.
        self._memory: dict[str, np.ndarray] = {}
        self._evicted = False
        self._client: OpenAI | None = None
        # Counters the CLI reports, so API usage is visible rather than implied.
        self.api_calls = 0
        self.texts_embedded = 0
        self.cache_hits = 0

    # ── cache ────────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection | None:
        """Open the cache, or return ``None`` and carry on without one.

        A cache that cannot be opened is a slow run, not a failed one — the same
        stance the JSON version took towards a corrupt file.
        """
        if not self.cache_path:
            return None
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            return conn
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "Ignoring unusable embedding cache %s: %s", self.cache_path, exc
            )
            return None

    def _evict(self) -> int:
        """Drop vectors nothing has asked for in ``EVICT_AFTER_DAYS``.

        Runs once per client, at the end of a call rather than the start, so the
        lookups have already refreshed what they touched. Sweeping first would
        delete an entry a day over the line and then immediately re-buy it —
        the failure the last-used policy exists to avoid, arriving by another
        route.

        A vector that only a *later* call in the same run would have wanted is
        still fair game, since nothing has claimed it yet; it gets re-embedded
        and re-stored. For the callers here that means an article absent for a
        week and republished today, which is rare and costs one text.
        """
        cutoff = time.time() - EVICT_AFTER_DAYS * 86400
        try:
            with self._lock, self._conn:
                removed = self._conn.execute(
                    "DELETE FROM embeddings WHERE last_used < ?", (cutoff,)
                ).rowcount
        except sqlite3.Error as exc:
            logger.warning("Embedding cache eviction failed: %s", exc)
            return 0
        if removed:
            logger.info(
                "Evicted %d embedding(s) unused for %d days", removed, EVICT_AFTER_DAYS
            )
        return removed

    def _lookup(self, keys: list[str]) -> dict[str, np.ndarray]:
        """Fetch just these keys, marking each hit as used now."""
        if self._conn is None:
            return {k: self._memory[k] for k in keys if k in self._memory}

        found: dict[str, np.ndarray] = {}
        now = time.time()
        try:
            with self._lock, self._conn:
                for chunk in _chunked(keys, _MAX_SQL_VARS):
                    marks = ",".join("?" * len(chunk))
                    rows = self._conn.execute(
                        f"SELECT key, vector FROM embeddings WHERE key IN ({marks})",
                        chunk,
                    ).fetchall()
                    if not rows:
                        continue
                    for key, blob in rows:
                        found[key] = np.frombuffer(blob, dtype=np.float32)
                    hit = [key for key, _ in rows]
                    self._conn.execute(
                        "UPDATE embeddings SET last_used = ? WHERE key IN "
                        f"({','.join('?' * len(hit))})",
                        [now, *hit],
                    )
        except sqlite3.Error as exc:
            logger.warning("Embedding cache lookup failed: %s", exc)
            return {}
        return found

    def _store(self, vectors: dict[str, np.ndarray]) -> None:
        if not vectors:
            return
        if self._conn is None:
            self._memory.update(vectors)
            return

        now = time.time()
        rows = [
            (key, self.model, np.asarray(v, dtype=np.float32).tobytes(), now)
            for key, v in vectors.items()
        ]
        try:
            with self._lock, self._conn:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO embeddings (key, model, vector, last_used)"
                    " VALUES (?, ?, ?, ?)",
                    rows,
                )
        except sqlite3.Error as exc:
            logger.warning("Could not write to the embedding cache: %s", exc)

    def _key(self, text: str) -> str:
        """Cache key over model + text, so switching models can't reuse vectors."""
        return hashlib.sha256(f"{self.model}\x00{text}".encode()).hexdigest()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

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
        keys = {t: self._key(t) for t in unique}

        vectors = self._lookup([keys[t] for t in unique])
        missing = [t for t in unique if keys[t] not in vectors]
        self.cache_hits += len(unique) - len(missing)

        fresh: dict[str, np.ndarray] = {}
        for start in range(0, len(missing), _BATCH_SIZE):
            batch = missing[start : start + _BATCH_SIZE]
            response = self._openai().embeddings.create(model=self.model, input=batch)
            self.api_calls += 1
            self.texts_embedded += len(batch)
            for text, item in zip(batch, response.data):
                fresh[keys[text]] = np.asarray(item.embedding, dtype=np.float32)

        if fresh:
            self._store(fresh)
            vectors.update(fresh)

        if missing:
            logger.info(
                "Embedded %d new texts, %d already cached (%d API call(s))",
                len(missing),
                len(unique) - len(missing),
                self.api_calls,
            )
        else:
            logger.info("All %d texts served from the embedding cache", len(unique))

        if self._conn is not None and not self._evicted:
            self._evicted = True
            self._evict()

        matrix = np.array([vectors[keys[t]] for t in prepared], dtype=np.float32)
        return _l2_normalize(matrix)
