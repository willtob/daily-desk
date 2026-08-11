"""Summary cache persistence — the part that costs money when it breaks.

``summarize_many`` runs six threads and saves the cache once at the end. What
decides whether it saves is the thing under test. Gating on ``api_calls`` looks
correct and passes any single-threaded check, because the counter only
undercounts when concurrent ``+=`` calls interleave — and the cost of that
undercount landing on zero is silent: the run succeeds, the digest is fine, and
the summaries that were just paid for are gone by the next run.

So the tests drive the gate directly rather than hoping to lose a race: one run
where nothing is new and must not write, one where the counter is forced to zero
and must write anyway.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from esp_news.summarize import Summarizer

JOBS = [(f"Title {i}", "Some Source", f"article body {i} " * 40) for i in range(8)]


class FakeResponses:
    """Stands in for ``client.responses``; records how often it was called."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return type("Resp", (), {"output_text": f"Summary of {kwargs['input'][:24]}"})()


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


@pytest.fixture
def summarizer_factory(tmp_path):
    """Build Summarizers that share one cache file and never touch the network."""
    cache_path = tmp_path / "summaries.json"
    clients: list[FakeClient] = []

    def make() -> Summarizer:
        s = Summarizer(cache_path=cache_path)
        client = FakeClient()
        clients.append(client)
        s._openai = lambda: client
        return s

    make.cache_path = cache_path
    make.clients = clients
    return make


def test_all_cache_hits_leaves_the_file_untouched(summarizer_factory):
    """Nothing new to persist must mean no write at all, not a rewrite."""
    first = summarizer_factory()
    first.summarize_many(JOBS)
    cache_path: Path = summarizer_factory.cache_path
    assert cache_path.exists()
    mtime_before = cache_path.stat().st_mtime_ns

    second = summarizer_factory()
    summaries = second.summarize_many(JOBS)

    assert second.cache_hits == len(JOBS)
    assert second.api_calls == 0
    assert second._cache_dirty is False
    assert all(summaries)
    assert cache_path.stat().st_mtime_ns == mtime_before
    # And the second run made no API calls at all.
    assert summarizer_factory.clients[1].responses.calls == 0


def test_summaries_persist_even_when_api_calls_is_lost(summarizer_factory, monkeypatch):
    """The race the flag exists for: every ``+=`` lost, counter stuck at zero.

    Simulated rather than raced, because a lost increment is not reproducible on
    demand — but the gate cannot tell a simulated zero from a raced one, which
    is exactly the point.
    """
    summarizer = summarizer_factory()
    real_summarize = Summarizer.summarize

    def lossy_summarize(self, *args):
        result = real_summarize(self, *args)
        self.api_calls = 0
        return result

    monkeypatch.setattr(Summarizer, "summarize", lossy_summarize)
    summaries = summarizer.summarize_many(JOBS)

    assert summarizer.api_calls == 0
    assert all(summaries)

    cache_path: Path = summarizer_factory.cache_path
    assert cache_path.exists(), "summaries paid for were never written to disk"
    assert len(json.loads(cache_path.read_text())) == len(JOBS)


def test_a_second_run_serves_the_persisted_summaries(summarizer_factory, monkeypatch):
    """The end the persistence is for: the next run pays nothing."""
    first = summarizer_factory()
    real_summarize = Summarizer.summarize

    def lossy_summarize(self, *args):
        result = real_summarize(self, *args)
        self.api_calls = 0
        return result

    monkeypatch.setattr(Summarizer, "summarize", lossy_summarize)
    expected = first.summarize_many(JOBS)
    monkeypatch.undo()

    second = summarizer_factory()
    assert second.summarize_many(JOBS) == expected
    assert second.cache_hits == len(JOBS)
    assert summarizer_factory.clients[1].responses.calls == 0


def test_a_failed_summary_alone_does_not_dirty_the_cache(summarizer_factory):
    """An empty model response is not something to persist."""
    summarizer = summarizer_factory()
    summarizer._openai = lambda: type(
        "C", (), {"responses": type("R", (), {"create": lambda self, **kw: type(
            "Resp", (), {"output_text": ""})()})()}
    )()

    summaries = summarizer.summarize_many(JOBS[:2])

    assert summaries == ["", ""]
    assert summarizer._cache_dirty is False
    assert not summarizer_factory.cache_path.exists()


def test_bumping_the_prompt_version_stops_the_old_summaries_being_served(
    summarizer_factory, monkeypatch
):
    """A prompt change must invalidate what the previous prompt produced.

    This is the discipline the PROMPT_VERSION comment describes, and it fails
    silently when skipped: the run succeeds, the cache is hit, and the new
    instructions never reach a single article. It bit the Catalan-to-English
    change directly — the Catalan feeds had summaries cached in Catalan, and
    without the bump the change would have looked like it did nothing.
    """
    first = summarizer_factory()
    first.summarize_many(JOBS)
    assert summarizer_factory.clients[0].responses.calls == len(JOBS)

    monkeypatch.setattr("esp_news.summarize.PROMPT_VERSION", "999")

    second = summarizer_factory()
    second.summarize_many(JOBS)

    assert second.cache_hits == 0, "stale summaries served under a changed prompt"
    assert summarizer_factory.clients[1].responses.calls == len(JOBS)


def test_catalan_is_summarized_in_english_and_other_languages_are_not(
    summarizer_factory,
):
    """The instruction reaches the model, and is scoped to Catalan alone.

    Asserting on the prompt rather than on a summary: the behaviour lives in an
    LLM, so a test that called one would be measuring the model. What this can
    pin down is that the rule is sent, and that it did not quietly widen into
    "translate everything" — the Spanish feeds are the biggest contributors here
    and their summaries are meant to stay in Spanish.
    """
    instructions = _instructions_sent(summarizer_factory)

    assert "Catalan" in instructions
    assert "write the summary in English" in instructions
    # The fallback for every other language has to survive alongside it.
    assert "same language as the article" in instructions
    assert "Spanish" not in instructions


def _instructions_sent(summarizer_factory) -> str:
    """The system prompt the summarizer actually sends for one article."""
    captured: dict[str, str] = {}

    summarizer = summarizer_factory()
    real_create = summarizer_factory.clients[0].responses.create

    def spy(**kwargs):
        captured["instructions"] = kwargs["instructions"]
        return real_create(**kwargs)

    summarizer_factory.clients[0].responses.create = spy
    summarizer.summarize("Títol", "beteve Cultura", "cos de l'article " * 20)
    return captured["instructions"]
