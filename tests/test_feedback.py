"""Tests for the verdict log and the article index.

The log is append-only and replayed on read, so almost everything worth testing
here is about what the *replay* concludes from a given sequence of lines rather
than about what any one call returns. The rest is damage tolerance: a store that
loses old verdicts because the last append was interrupted would be worse than
no store, because the loss is silent and the data is not regenerable.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from esp_news.feedback import (
    DISLIKE,
    LIKE,
    ArticleIndex,
    FeedbackStore,
    index_digest,
)
from esp_news.models import Article


@pytest.fixture
def store(tmp_path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.jsonl")


def article(title: str, *, url: str | None = None, area: str = "spain") -> Article:
    return Article(
        title=title,
        url=url or f"https://example.test/{title}",
        source="Test Feed",
        theme="t",
        summary="body text",
        matched_area=area,
        score=0.5,
    )


# ── replay ───────────────────────────────────────────────────────────────────


def test_a_verdict_survives_a_round_trip(store):
    store.record(LIKE, url="https://example.test/a", title="A", text="A body",
                 matched_area="spain", score=0.51)

    record = FeedbackStore(store.path).get("https://example.test/a")
    assert record is not None
    assert (record.verdict, record.title, record.text) == (LIKE, "A", "A body")
    assert record.matched_area == "spain"


def test_re_recording_the_same_url_updates_rather_than_duplicates(store):
    """Idempotency is the client's whole safety net for a retried request."""
    for _ in range(3):
        store.record(LIKE, url="https://example.test/a", matched_area="spain")

    assert len(store) == 1
    assert store.get("https://example.test/a").verdict == LIKE
    # ...but the log kept every line. Changing your mind is data.
    assert len(store.path.read_text().strip().splitlines()) == 3


def test_the_newest_verdict_wins(store):
    store.record(LIKE, url="https://example.test/a", matched_area="spain")
    store.record(DISLIKE, url="https://example.test/a", matched_area="spain")

    assert store.get("https://example.test/a").verdict == DISLIKE
    assert store.texts_by_area(LIKE) == {}


def test_clearing_removes_the_verdict_without_rewriting_the_log(store):
    store.record(LIKE, url="https://example.test/a", matched_area="spain")
    lines_before = len(store.path.read_text().splitlines())

    assert store.clear("https://example.test/a") is True
    assert store.get("https://example.test/a") is None
    assert len(store) == 0
    # A tombstone, not an edit: the file only ever grew.
    assert len(store.path.read_text().splitlines()) == lines_before + 1


def test_clearing_something_unrated_is_a_no_op_not_an_error(store):
    assert store.clear("https://example.test/never-seen") is False
    assert len(store) == 0


def test_a_cleared_article_can_be_rated_again(store):
    """A mis-swipe, an undo, then the verdict you meant."""
    store.record(LIKE, url="https://example.test/a", matched_area="spain")
    store.clear("https://example.test/a")
    store.record(DISLIKE, url="https://example.test/a", matched_area="spain")

    assert store.get("https://example.test/a").verdict == DISLIKE


def test_urls_are_matched_the_way_the_rest_of_the_pipeline_matches_them(store):
    """Tracking params must not create a second verdict on one article."""
    store.record(LIKE, url="https://example.test/a", matched_area="spain")
    store.record(
        DISLIKE, url="https://example.test/a?utm_source=rss", matched_area="spain"
    )

    assert len(store) == 1
    assert store.get("https://example.test/a").verdict == DISLIKE


def test_an_unknown_verdict_is_refused(store):
    with pytest.raises(ValueError, match="unknown verdict"):
        store.record("meh", url="https://example.test/a")


# ── damage tolerance ─────────────────────────────────────────────────────────


def test_a_torn_final_line_costs_only_that_line(store):
    """What an interrupted append actually looks like on disk."""
    store.record(LIKE, url="https://example.test/a", matched_area="spain")
    store.record(LIKE, url="https://example.test/b", matched_area="florida")
    with open(store.path, "a", encoding="utf-8") as handle:
        handle.write('{"verdict": "like", "url": "https://example.test/c", "ti')

    current = FeedbackStore(store.path).verdicts()
    assert sorted(r.url for r in current.values()) == [
        "https://example.test/a",
        "https://example.test/b",
    ]


def test_a_missing_log_is_an_empty_one(tmp_path):
    """Cold start: no file, no verdicts, no exception."""
    store = FeedbackStore(tmp_path / "nothing-here.jsonl")
    assert len(store) == 0
    assert store.verdicts() == {}
    assert store.texts_by_area(LIKE) == {}


# ── grouping for the scorer ──────────────────────────────────────────────────


def test_texts_are_grouped_by_the_area_the_verdict_was_recorded_against(store):
    store.record(LIKE, url="https://example.test/a", text="one", matched_area="spain")
    store.record(LIKE, url="https://example.test/b", text="two", matched_area="spain")
    store.record(LIKE, url="https://example.test/c", text="three", matched_area="florida")
    store.record(DISLIKE, url="https://example.test/d", text="four", matched_area="spain")

    assert store.texts_by_area(LIKE) == {
        "spain": ["one", "two"],
        "florida": ["three"],
    }
    assert store.texts_by_area(DISLIKE) == {"spain": ["four"]}


def test_records_with_nothing_to_embed_are_dropped(store):
    """A verdict with no text or no area cannot be attached to anything."""
    store.record(LIKE, url="https://example.test/a", text="", matched_area="spain")
    store.record(LIKE, url="https://example.test/b", text="body", matched_area=None)

    assert store.texts_by_area(LIKE) == {}


# ── the article index ────────────────────────────────────────────────────────


def test_the_index_stores_the_text_the_scorer_would_embed(tmp_path):
    """The point of the index: the client's URL, the scorer's text."""
    art = article("Something happened")
    index_digest([art], path=tmp_path / "index.json")

    entry = ArticleIndex(tmp_path / "index.json").get(art.url)
    assert entry["text"] == art.embedding_text
    assert entry["text"].startswith("Something happened")
    assert entry["matched_area"] == "spain"


def test_the_index_merges_across_runs(tmp_path):
    """A verdict on yesterday's digest still has to resolve."""
    path = tmp_path / "index.json"
    index_digest([article("monday")], path=path)
    index_digest([article("tuesday")], path=path)

    index = ArticleIndex(path)
    assert index.get("https://example.test/monday") is not None
    assert index.get("https://example.test/tuesday") is not None


def test_the_index_forgets_articles_older_than_the_retention_window(tmp_path):
    path = tmp_path / "index.json"
    index = ArticleIndex(path)
    index.add([article("ancient")], when=date.today() - timedelta(days=90))
    index.add([article("recent")])
    index.save()

    assert ArticleIndex(path).prune() == 1
    index = ArticleIndex(path)
    index.prune()
    assert index.get("https://example.test/recent") is not None


def test_indexing_never_takes_the_pipeline_down_with_it(tmp_path):
    """A bad index path must cost the index, not the digest that just ran."""
    unwritable = tmp_path / "file-not-a-dir"
    unwritable.write_text("blocking the path")

    index_digest([article("a")], path=unwritable / "index.json")  # must not raise
