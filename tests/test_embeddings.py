"""Embedding cache — hits, misses, and what eviction is allowed to throw away.

The eviction tests carry the most weight. Evicting on creation date instead of
last use would pass any test that only checks "old entries go away", and the
damage would be invisible: the run still works, it just silently re-buys the
interest-profile references every week. So there is a test for the entry that
is old *and* still in use, which is the case the two policies disagree on.

No test here reaches the network. The fake client returns deterministic vectors
derived from the text, so a cached vector and a freshly fetched one are only
distinguishable by whether the fake was called.
"""

from __future__ import annotations

import sqlite3
import time

import numpy as np
import pytest

from esp_news.embeddings import (
    EVICT_AFTER_DAYS,
    EmbeddingClient,
    MissingAPIKeyError,
)

DIM = 8
DAY = 86400


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls = 0
        self.texts: list[str] = []

    def create(self, *, model, input):
        self.calls += 1
        self.texts.extend(input)
        data = []
        for text in input:
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            data.append(type("Item", (), {"embedding": rng.random(DIM).tolist()})())
        return type("Resp", (), {"data": data})()


class FakeOpenAI:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings()


@pytest.fixture
def client_factory(tmp_path):
    """Build clients sharing one database, each with its own fake API."""
    db = tmp_path / "embeddings.db"
    made: list[EmbeddingClient] = []

    def make(**kwargs) -> EmbeddingClient:
        client = EmbeddingClient(cache_path=kwargs.pop("cache_path", db), **kwargs)
        fake = FakeOpenAI()
        client._openai = lambda: fake
        client.fake = fake
        made.append(client)
        return client

    make.db = db
    make.made = made
    yield make
    for client in made:
        client.close()


def _row_count(db) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    finally:
        conn.close()


def _backdate(db, *, days: float, key: str | None = None) -> None:
    """Move last_used into the past, so eviction can be tested without waiting."""
    when = time.time() - days * DAY
    conn = sqlite3.connect(str(db))
    try:
        if key is None:
            conn.execute("UPDATE embeddings SET last_used = ?", (when,))
        else:
            conn.execute(
                "UPDATE embeddings SET last_used = ? WHERE key = ?", (when, key)
            )
        conn.commit()
    finally:
        conn.close()


# ── basic behaviour ──────────────────────────────────────────────────────────


def test_first_call_is_a_miss_and_second_is_a_hit(client_factory):
    texts = ["alpha", "beta", "gamma"]

    first = client_factory()
    a = first.embed(texts)
    assert first.fake.embeddings.calls == 1
    assert first.texts_embedded == 3
    assert first.cache_hits == 0

    second = client_factory()
    b = second.embed(texts)
    assert second.fake.embeddings.calls == 0, "cached texts still hit the API"
    assert second.cache_hits == 3
    assert second.texts_embedded == 0
    np.testing.assert_array_equal(a, b)


def test_only_the_missing_texts_are_fetched(client_factory):
    client_factory().embed(["alpha", "beta"])

    client = client_factory()
    client.embed(["alpha", "beta", "gamma"])

    assert client.fake.embeddings.texts == ["gamma"]
    assert client.cache_hits == 2
    assert client.texts_embedded == 1


def test_vectors_are_normalized_and_in_input_order(client_factory):
    texts = ["one", "two", "three", "two"]
    client = client_factory()

    matrix = client.embed(texts)

    assert matrix.shape == (4, DIM)
    np.testing.assert_allclose(np.linalg.norm(matrix, axis=1), 1.0, rtol=1e-6)
    # Row order follows the input, repeats included.
    np.testing.assert_array_equal(matrix[1], matrix[3])
    assert not np.allclose(matrix[0], matrix[1])
    # A repeated text is paid for once.
    assert client.texts_embedded == 3


def test_order_survives_a_partial_cache(client_factory):
    """The rows must line up with the input even when some came from cache."""
    texts = ["one", "two", "three", "four"]
    warm = client_factory()
    expected = warm.embed(texts)

    partial = client_factory()
    partial.embed(["two", "four"])  # already cached by `warm`; no-op but explicit

    mixed = client_factory()
    np.testing.assert_array_equal(mixed.embed(texts), expected)


def test_empty_input_returns_an_empty_matrix(client_factory):
    assert client_factory().embed([]).shape == (0, 0)


def test_text_is_capped_before_it_is_sent(client_factory):
    client = client_factory()
    client.embed(["x" * 9000])
    assert len(client.fake.embeddings.texts[0]) == 8000


def test_a_different_model_does_not_reuse_vectors(client_factory):
    client_factory(model="text-embedding-3-small").embed(["alpha"])

    other = client_factory(model="text-embedding-3-large")
    other.embed(["alpha"])

    assert other.cache_hits == 0
    assert other.texts_embedded == 1


def test_no_cache_path_still_embeds(client_factory):
    client = client_factory(cache_path=None)
    matrix = client.embed(["alpha", "alpha"])
    assert matrix.shape == (2, DIM)
    assert client.texts_embedded == 1
    assert not client_factory.db.exists()


def test_missing_api_key_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = EmbeddingClient(cache_path=tmp_path / "e.db")
    try:
        with pytest.raises(MissingAPIKeyError):
            client.embed(["alpha"])
    finally:
        client.close()


# ── storage ──────────────────────────────────────────────────────────────────


def test_vectors_are_stored_as_float32_blobs(client_factory):
    client_factory().embed(["alpha"])

    conn = sqlite3.connect(str(client_factory.db))
    try:
        blob, model = conn.execute(
            "SELECT vector, model FROM embeddings"
        ).fetchone()
    finally:
        conn.close()

    assert len(blob) == DIM * 4, "not a float32 blob"
    assert model == "text-embedding-3-small"
    assert np.frombuffer(blob, dtype=np.float32).shape == (DIM,)


def test_an_unusable_database_degrades_to_no_cache(tmp_path, caplog):
    broken = tmp_path / "broken.db"
    broken.write_text("this is not a database")

    client = EmbeddingClient(cache_path=broken)
    fake = FakeOpenAI()
    client._openai = lambda: fake
    try:
        matrix = client.embed(["alpha"])
    finally:
        client.close()

    assert matrix.shape == (1, DIM)


# ── eviction ─────────────────────────────────────────────────────────────────


def test_entries_unused_past_the_window_are_evicted(client_factory):
    client_factory().embed(["alpha", "beta"])
    assert _row_count(client_factory.db) == 2

    _backdate(client_factory.db, days=EVICT_AFTER_DAYS + 1)
    client_factory().embed(["something else"])  # a later run sweeps

    assert _row_count(client_factory.db) == 1  # only the new one is left


def test_entries_inside_the_window_survive(client_factory):
    client_factory().embed(["alpha", "beta"])

    _backdate(client_factory.db, days=EVICT_AFTER_DAYS - 1)
    client_factory().embed(["something else"])

    assert _row_count(client_factory.db) == 3


def test_an_old_but_still_used_entry_survives(client_factory):
    """The case last-used and created-at eviction disagree on.

    A profile reference is written once and never rewritten, so by creation
    date it is always ancient. Reading it must keep it alive.
    """
    client_factory().embed(["profile reference"])
    _backdate(client_factory.db, days=EVICT_AFTER_DAYS + 30)

    reader = client_factory()
    reader.embed(["profile reference"])
    assert reader.cache_hits == 1, "the entry was gone before it could be read"

    client_factory().embed(["unrelated"])  # a later run sweeps

    assert _row_count(client_factory.db) == 2, (
        "an entry read moments ago was evicted — eviction is keyed on creation, "
        "not last use"
    )


def test_a_call_never_evicts_what_it_just_used(client_factory):
    """The sweep runs after the lookups, so one call cannot undo itself."""
    client_factory().embed(["long lived reference"])
    _backdate(client_factory.db, days=EVICT_AFTER_DAYS + 100)

    client = client_factory()
    client.embed(["long lived reference"])

    assert client.cache_hits == 1
    assert _row_count(client_factory.db) == 1


def test_a_hit_refreshes_last_used(client_factory):
    client_factory().embed(["alpha"])
    _backdate(client_factory.db, days=EVICT_AFTER_DAYS + 30)

    conn = sqlite3.connect(str(client_factory.db))
    before = conn.execute("SELECT last_used FROM embeddings").fetchone()[0]
    conn.close()

    client_factory().embed(["alpha"])

    conn = sqlite3.connect(str(client_factory.db))
    after = conn.execute("SELECT last_used FROM embeddings").fetchone()[0]
    conn.close()

    assert after > before
    assert time.time() - after < 60


def test_eviction_spares_the_entries_a_run_is_using(client_factory):
    """One stale entry, one fresh: only the stale one goes."""
    client_factory().embed(["stale", "fresh"])
    stale_key = EmbeddingClient(cache_path=None)._key("stale")
    _backdate(client_factory.db, days=EVICT_AFTER_DAYS + 5, key=stale_key)

    survivor = client_factory()
    survivor.embed(["fresh"])

    assert survivor.cache_hits == 1
    assert _row_count(client_factory.db) == 1
