"""Tests for the feedback endpoints — the contract in docs/feedback-api.md.

A separate session builds the macOS gesture against that document, so these
exist to keep the document honest. Anything asserted here is something a client
author is entitled to rely on: the status codes, the idempotency, and above all
that clearing works, because an accidental swipe with no undo is worse than no
gesture at all.

The stores are redirected at temp files for the whole module. Without that these
tests would write verdicts into the real log, which is tracked, personal, and
not regenerable.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from esp_news import api, feedback
from esp_news.feedback import FeedbackStore
from esp_news.models import Article

DIGEST_URL = "https://example.test/on-the-page"
INDEXED_URL = "https://example.test/indexed"


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    """Point both stores and the served digest at temp files."""
    monkeypatch.setattr(feedback, "DEFAULT_FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    monkeypatch.setattr(feedback, "DEFAULT_INDEX_PATH", tmp_path / "index.json")

    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps(
            {
                "generated": "2026-08-09",
                "count": 1,
                "articles": [
                    {
                        "title": "On the page",
                        "summary": "The LLM summary the client was shown.",
                        "source": "Test Feed",
                        "matched_area": "spain",
                        "score": 0.51,
                        "url": DIGEST_URL,
                        "wildcard": False,
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(api, "LATEST_JSON", latest)
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.app)


@pytest.fixture
def indexed() -> Article:
    """An article the pipeline has scored, so its embedded text is on record."""
    art = Article(
        title="Indexed story",
        url=INDEXED_URL,
        source="Test Feed",
        theme="t",
        summary="the raw rss blurb that was actually embedded",
        matched_area="florida",
        score=0.44,
    )
    feedback.index_digest([art])
    return art


# ── recording ────────────────────────────────────────────────────────────────


def test_a_like_is_recorded_against_the_text_that_was_embedded(client, indexed):
    """The whole reason the index exists.

    What the client was shown is the LLM summary; what the scorer embedded is
    the title plus the raw RSS blurb. A verdict has to store the second one.
    """
    response = client.post("/feedback", json={"url": INDEXED_URL, "verdict": "like"})
    assert response.status_code == 200

    record = response.json()["record"]
    assert record["verdict"] == "like"
    assert record["text"] == indexed.embedding_text
    assert record["text_source"] == "embedded"
    assert record["matched_area"] == "florida"


def test_an_article_only_in_the_served_digest_still_takes_a_verdict(client):
    """A verdict on a digest older than the index must not be refused.

    It records the displayed text instead, and says so, rather than dropping a
    tap on the floor.
    """
    response = client.post("/feedback", json={"url": DIGEST_URL, "verdict": "dislike"})
    assert response.status_code == 200

    record = response.json()["record"]
    assert record["text_source"] == "display"
    assert "LLM summary" in record["text"]
    assert record["matched_area"] == "spain"


def test_an_unknown_article_is_a_404(client):
    response = client.post(
        "/feedback", json={"url": "https://example.test/never", "verdict": "like"}
    )
    assert response.status_code == 404


def test_an_unknown_verdict_is_a_422(client, indexed):
    response = client.post("/feedback", json={"url": INDEXED_URL, "verdict": "meh"})
    assert response.status_code == 422


def test_recording_the_same_verdict_twice_leaves_one_verdict(client, indexed):
    """Idempotency: a retried request must not double-count."""
    for _ in range(2):
        assert client.post(
            "/feedback", json={"url": INDEXED_URL, "verdict": "like"}
        ).status_code == 200

    body = client.get("/feedback").json()
    assert body["count"] == 1
    assert body["likes"] == 1


def test_a_verdict_can_be_changed(client, indexed):
    client.post("/feedback", json={"url": INDEXED_URL, "verdict": "like"})
    client.post("/feedback", json={"url": INDEXED_URL, "verdict": "dislike"})

    body = client.get("/feedback").json()
    assert (body["count"], body["likes"], body["dislikes"]) == (1, 0, 1)


# ── clearing ─────────────────────────────────────────────────────────────────


def test_a_verdict_can_be_cleared_by_post(client, indexed):
    client.post("/feedback", json={"url": INDEXED_URL, "verdict": "like"})

    response = client.post("/feedback", json={"url": INDEXED_URL, "verdict": "clear"})
    assert response.status_code == 200
    assert response.json() == {"url": INDEXED_URL, "verdict": None, "cleared": True}
    assert client.get("/feedback").json()["count"] == 0


def test_a_verdict_can_be_cleared_by_delete(client, indexed):
    """The same operation, for clients that expect DELETE to be the undo."""
    client.post("/feedback", json={"url": INDEXED_URL, "verdict": "like"})

    response = client.request("DELETE", "/feedback", params={"url": INDEXED_URL})
    assert response.status_code == 200
    assert response.json()["cleared"] is True
    assert client.get("/feedback").json()["count"] == 0


def test_clearing_nothing_succeeds_and_says_so(client):
    """A double-undo is not an error — the end state is the one asked for."""
    response = client.post(
        "/feedback", json={"url": "https://example.test/never", "verdict": "clear"}
    )
    assert response.status_code == 200
    assert response.json()["cleared"] is False


# ── reading back ─────────────────────────────────────────────────────────────


def test_reading_one_articles_verdict(client, indexed):
    client.post("/feedback", json={"url": INDEXED_URL, "verdict": "like"})

    body = client.get("/feedback", params={"url": INDEXED_URL}).json()
    assert body["verdict"] == "like"


def test_an_unrated_article_reads_as_null_not_as_an_error(client):
    """"No verdict" is a state a card renders, not a failure."""
    body = client.get("/feedback", params={"url": "https://example.test/never"}).json()
    assert body == {
        "url": "https://example.test/never",
        "verdict": None,
        "record": None,
    }


def test_the_stored_text_is_left_out_unless_asked_for(client, indexed):
    """A thumbs-up renderer does not need a kilobyte of article per card."""
    client.post("/feedback", json={"url": INDEXED_URL, "verdict": "like"})

    assert "text" not in client.get("/feedback").json()["verdicts"][0]
    with_text = client.get("/feedback", params={"include_text": True}).json()
    assert with_text["verdicts"][0]["text"] == indexed.embedding_text


def test_an_empty_store_reads_as_an_empty_list(client):
    """Cold start, which is the state the client ships in."""
    assert client.get("/feedback").json() == {
        "count": 0,
        "likes": 0,
        "dislikes": 0,
        "verdicts": [],
    }


def test_the_verdicts_reach_the_scorer_grouped_by_area(client, indexed):
    """End to end: what the endpoint writes is what score_articles reads."""
    client.post("/feedback", json={"url": INDEXED_URL, "verdict": "like"})

    assert FeedbackStore().texts_by_area("like") == {
        "florida": [indexed.embedding_text]
    }
