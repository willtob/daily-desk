"""The /learn endpoints and the store underneath them.

Nothing here calls OpenAI: the grader is replaced through FastAPI's dependency
overrides, so what is under test is the plumbing — what gets stored, what gets
returned, and what happens on the paths that only occur when something has gone
wrong. The grading *quality* lives in an LLM and a test that called one would
be measuring the model, not this code.

Two things get particular attention:

  * **The covers hint must not leave the server on /learn/topic.** It is the
    grading checklist. Leak it into the draw response and the exercise becomes
    reading comprehension — the single thing this feature exists not to be, and
    a leak a UI author would happily render without knowing.
  * **One grade per session.** A double-tapped submit button must not add two
    qualifying sessions to one day. The unique index is the enforcement; this
    checks the 409 rather than a second row.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from esp_news.api import app
from esp_news.learn.api import get_bank, get_grader, get_store
from esp_news.learn.grade import Grade, GradeResult
from esp_news.learn.store import AlreadyGradedError, LearnStore
from esp_news.learn.topics import LearnSettings, Topic, TopicBank

TOPICS = [
    Topic(
        id=f"{difficulty}_{i}",
        name=f"{difficulty.title()} topic {i}",
        difficulty=difficulty,
        covers=f"The checklist for {difficulty} topic {i}, long enough to be real.",
    )
    for difficulty in ("intro", "intermediate", "advanced")
    for i in range(3)
]


class FakeGrader:
    """Returns a fixed grade and records what it was asked to grade."""

    def __init__(self, score: int = 8) -> None:
        self.score = score
        self.calls: list[dict] = []
        self.error: Exception | None = None

    def grade(self, topic, explanation, *, source="text") -> GradeResult:
        self.calls.append(
            {"topic": topic, "explanation": explanation, "source": source}
        )
        if self.error:
            raise self.error
        return GradeResult(
            grade=Grade(
                score=self.score,
                feedback="Specific feedback about the mechanism.",
                missed_concepts=["did not mention the update step"],
                strengths=["named the objective being minimised"],
            ),
            model="fake-model",
            prompt_version="test",
            latency_ms=1234,
        )


@pytest.fixture
def store(tmp_path) -> LearnStore:
    s = LearnStore(tmp_path / "learn.db")
    yield s
    s.close()


@pytest.fixture
def bank() -> TopicBank:
    # A fixed zone so day boundaries in these tests do not depend on where the
    # suite runs, and a cooldown of zero so draws are not filtered by history.
    return TopicBank(
        settings=LearnSettings(
            pass_score=7, timezone="Europe/Madrid", repeat_cooldown_days=0
        ),
        topics=TOPICS,
    )


@pytest.fixture
def grader() -> FakeGrader:
    return FakeGrader()


@pytest.fixture
def client(bank, store, grader):
    app.dependency_overrides[get_bank] = lambda: bank
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_grader] = lambda: grader
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


MADRID = ZoneInfo("Europe/Madrid")


def local_noon(days_ago: int = 0, *, hour: int = 12) -> datetime:
    """A UTC instant that is midday ``days_ago`` local days back.

    The stats endpoint resolves "today" from the real clock, so these fixtures
    have to be positioned relative to the real local day. Using
    ``datetime.now() - timedelta(days=n)`` instead looks equivalent and is not:
    a suite run just after local midnight puts "now" and "two hours ago" on
    different calendar days, and the same arithmetic drifts by an hour across a
    DST boundary. Midday is far enough from either edge that neither happens.
    """
    day = datetime.now(timezone.utc).astimezone(MADRID).date() - timedelta(days=days_ago)
    return datetime.combine(day, time(hour), tzinfo=MADRID).astimezone(timezone.utc)


def seed_session(store: LearnStore, *, topic_id: str, started_at: datetime, score: int):
    """A completed session, backdated.

    Goes through the real write paths and only rewrites ``started_at``, so the
    rows are shaped exactly as production writes them.
    """
    session = store.start_session(topic_id)
    store.record_grade(
        session.id,
        score=score,
        feedback="f",
        missed_concepts=[],
        strengths=[],
        explanation="an explanation",
        source="text",
        model="fake-model",
        prompt_version="test",
        latency_ms=1,
    )
    with store._conn:
        store._conn.execute(
            "UPDATE sessions SET started_at = ? WHERE id = ?",
            (started_at.isoformat(), session.id),
        )
    return session.id


# ── GET /learn/topic ─────────────────────────────────────────────────────────


def test_draw_returns_a_topic_and_its_difficulty(client):
    body = client.get("/learn/topic").json()
    assert body["topic_id"] in {t.id for t in TOPICS}
    assert body["difficulty"] in {"intro", "intermediate", "advanced"}
    assert body["name"]


def test_draw_never_leaks_the_covers_hint(client):
    """The checklist must not reach the client before the explanation.

    Asserted against the raw response text rather than the parsed keys, so an
    extra field, a nested object, or a debug echo all fail this.
    """
    raw = client.get("/learn/topic").text
    assert "covers" not in raw
    for topic in TOPICS:
        assert topic.covers not in raw


def test_drawing_does_not_start_a_session_or_burn_the_cooldown(client, store):
    """Refreshing for a different topic has to be free."""
    for _ in range(5):
        client.get("/learn/topic")
    assert store.last_seen() == {}
    assert store.session_count() == 0


# ── POST /learn/session/start ────────────────────────────────────────────────


def test_starting_a_session_returns_an_id_and_a_timestamp(client):
    response = client.post("/learn/session/start", json={"topic_id": "intro_0"})
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"]
    assert body["topic_id"] == "intro_0"
    assert datetime.fromisoformat(body["started_at"]).tzinfo is not None


def test_starting_records_the_topic_as_seen(client, store):
    client.post("/learn/session/start", json={"topic_id": "intro_0"})
    client.post("/learn/session/start", json={"topic_id": "intro_0"})
    seen = store.last_seen()
    assert set(seen) == {"intro_0"}


def test_an_unknown_topic_is_rejected(client):
    response = client.post("/learn/session/start", json={"topic_id": "nope"})
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_two_sessions_get_different_ids(client):
    ids = {
        client.post("/learn/session/start", json={"topic_id": "intro_0"}).json()[
            "session_id"
        ]
        for _ in range(5)
    }
    assert len(ids) == 5


# ── POST /learn/grade ────────────────────────────────────────────────────────


def start(client, topic_id="intro_0") -> str:
    return client.post("/learn/session/start", json={"topic_id": topic_id}).json()[
        "session_id"
    ]


def test_grading_returns_the_structured_grade(client):
    session_id = start(client)
    body = client.post(
        "/learn/grade",
        json={"session_id": session_id, "explanation_text": "my explanation"},
    ).json()

    assert body["score"] == 8
    assert body["feedback"]
    assert body["missed_concepts"] == ["did not mention the update step"]
    assert body["strengths"] == ["named the objective being minimised"]
    assert body["counted"] is True
    assert body["pass_score"] == 7
    assert body["session_id"] == session_id


def test_the_grader_is_given_the_topics_covers_hint(client, grader):
    """The rubric anchor. Without it every grade is generic by construction."""
    session_id = start(client, "advanced_2")
    client.post(
        "/learn/grade",
        json={"session_id": session_id, "explanation_text": "words"},
    )
    assert grader.calls[0]["topic"].id == "advanced_2"
    assert grader.calls[0]["topic"].covers


def test_a_grade_below_the_threshold_does_not_count(client, grader):
    grader.score = 6
    session_id = start(client)
    body = client.post(
        "/learn/grade",
        json={"session_id": session_id, "explanation_text": "thin explanation"},
    ).json()
    assert body["score"] == 6
    assert body["counted"] is False


def test_the_threshold_is_inclusive(client, grader):
    grader.score = 7
    session_id = start(client)
    body = client.post(
        "/learn/grade", json={"session_id": session_id, "explanation_text": "x"}
    ).json()
    assert body["counted"] is True


def test_grading_stores_everything_needed_to_audit_it(client, store):
    """Prompt version, model and latency, so the rubric can be iterated on.

    Without these a stored grade cannot be attributed to the rubric that
    produced it, and every past grade becomes uninterpretable the first time
    the prompt changes.
    """
    session_id = start(client)
    client.post(
        "/learn/grade",
        json={
            "session_id": session_id,
            "explanation_text": "my full explanation",
            "source": "text",
        },
    )
    row = store._conn.execute(
        "SELECT * FROM grades WHERE session_id = ?", (session_id,)
    ).fetchone()

    assert row["model"] == "fake-model"
    assert row["prompt_version"] == "test"
    assert row["latency_ms"] == 1234
    assert row["source"] == "text"
    assert row["explanation"] == "my full explanation"
    assert json.loads(row["missed_concepts"]) == ["did not mention the update step"]


def test_a_transcript_is_accepted_and_recorded_as_one(client, store, grader):
    """Speech input has to be a value change, not a schema change.

    The same request shape carries it, the same rubric grades it, and the row
    says which it was so the two can be compared later rather than pooled.
    """
    session_id = start(client)
    response = client.post(
        "/learn/grade",
        json={
            "session_id": session_id,
            "explanation_text": "so basically the way this works is",
            "source": "transcript",
        },
    )
    assert response.status_code == 200
    assert grader.calls[0]["source"] == "transcript"
    row = store._conn.execute(
        "SELECT source FROM grades WHERE session_id = ?", (session_id,)
    ).fetchone()
    assert row["source"] == "transcript"


def test_an_unknown_source_is_rejected(client):
    session_id = start(client)
    response = client.post(
        "/learn/grade",
        json={"session_id": session_id, "explanation_text": "x", "source": "telepathy"},
    )
    assert response.status_code == 422


def test_grading_an_unknown_session_is_a_404(client):
    response = client.post(
        "/learn/grade", json={"session_id": "deadbeef", "explanation_text": "x"}
    )
    assert response.status_code == 404


def test_a_session_cannot_be_graded_twice(client, store):
    """The double-tapped submit button, which would otherwise inflate a streak."""
    session_id = start(client)
    payload = {"session_id": session_id, "explanation_text": "my explanation"}

    assert client.post("/learn/grade", json=payload).status_code == 200
    second = client.post("/learn/grade", json=payload)

    assert second.status_code == 409
    assert store.session_count() == 1


def test_grading_a_topic_that_left_topics_yaml_is_a_409(client, store, bank):
    """A session outliving its topic must say so rather than invent a checklist."""
    session_id = start(client, "intro_0")
    bank.topics = [t for t in bank.topics if t.id != "intro_0"]

    response = client.post(
        "/learn/grade", json={"session_id": session_id, "explanation_text": "x"}
    )
    assert response.status_code == 409
    assert "intro_0" in response.json()["detail"]


def test_an_empty_explanation_is_a_400_and_is_not_stored(client, store, grader):
    grader.error = ValueError("nothing to grade — the explanation is empty")
    session_id = start(client)
    response = client.post(
        "/learn/grade", json={"session_id": session_id, "explanation_text": "   "}
    )
    assert response.status_code == 400
    assert store.session_count() == 0


def test_a_grader_timeout_is_a_504(client, grader):
    """Reads as 'try again', which is what it is — nothing was recorded."""
    from openai import APITimeoutError

    grader.error = APITimeoutError(request=None)
    session_id = start(client)
    response = client.post(
        "/learn/grade", json={"session_id": session_id, "explanation_text": "x"}
    )
    assert response.status_code == 504


def test_a_missing_api_key_is_a_503(client, grader):
    from esp_news.embeddings import MissingAPIKeyError

    grader.error = MissingAPIKeyError("OPENAI_API_KEY is not set")
    session_id = start(client)
    response = client.post(
        "/learn/grade", json={"session_id": session_id, "explanation_text": "x"}
    )
    assert response.status_code == 503


def test_a_failed_grade_leaves_the_session_regradable(client, grader):
    """A 502 must not consume the session — the explanation is still good."""
    from openai import APIError

    grader.error = APIError("boom", request=None, body=None)
    session_id = start(client)
    assert (
        client.post(
            "/learn/grade", json={"session_id": session_id, "explanation_text": "x"}
        ).status_code
        == 502
    )

    grader.error = None
    assert (
        client.post(
            "/learn/grade", json={"session_id": session_id, "explanation_text": "x"}
        ).status_code
        == 200
    )


# ── GET /learn/stats ─────────────────────────────────────────────────────────


def test_stats_on_an_empty_store(client):
    body = client.get("/learn/stats").json()
    assert body["current_streak"] == 0
    assert body["longest_streak"] == 0
    assert body["sessions_completed"] == 0
    assert body["rolling_average"] is None
    assert body["average_all_time"] is None
    assert body["last_session_at"] is None


def test_stats_counts_a_run_of_qualifying_days(client, store):
    for days_ago in range(4):
        seed_session(
            store, topic_id="intro_0", started_at=local_noon(days_ago), score=8
        )

    body = client.get("/learn/stats").json()
    assert body["current_streak"] == 4
    assert body["longest_streak"] == 4
    assert body["sessions_completed"] == 4
    assert body["rolling_average"] == 8.0


def test_a_failing_session_counts_as_a_session_but_not_as_a_streak_day(client, store):
    """The distinction the pass_score exists for.

    Showing up and explaining badly is still a session — it belongs in the
    count and in the average — but it does not hold the streak up.
    """
    seed_session(store, topic_id="intro_0", started_at=local_noon(0), score=4)
    seed_session(store, topic_id="intro_1", started_at=local_noon(1), score=9)

    body = client.get("/learn/stats").json()
    assert body["sessions_completed"] == 2
    assert body["current_streak"] == 1  # yesterday's 9 only
    assert body["average_all_time"] == 6.5


def test_two_sessions_in_one_day_are_one_streak_day(client, store):
    seed_session(store, topic_id="intro_0", started_at=local_noon(0, hour=21), score=8)
    seed_session(store, topic_id="intro_1", started_at=local_noon(0, hour=9), score=9)

    body = client.get("/learn/stats").json()
    assert body["sessions_completed"] == 2
    assert body["current_streak"] == 1


def test_a_broken_streak_keeps_the_longest(client, store):
    for days_ago in (10, 11, 12, 13, 14):
        seed_session(
            store, topic_id="intro_0", started_at=local_noon(days_ago), score=8
        )
    seed_session(store, topic_id="intro_1", started_at=local_noon(0), score=8)

    body = client.get("/learn/stats").json()
    assert body["current_streak"] == 1
    assert body["longest_streak"] == 5


def test_the_rolling_window_is_honoured(client, store):
    """The rolling average must read recent sessions, not all of them."""
    for days_ago, score in enumerate([9, 9, 9, 2, 2, 2, 2]):
        seed_session(
            store, topic_id="intro_0", started_at=local_noon(days_ago), score=score
        )

    body = client.get("/learn/stats?window=3").json()
    assert body["rolling_window"] == 3
    assert body["rolling_average"] == 9.0
    assert body["average_all_time"] == round(35 / 7, 2)


def test_an_out_of_range_window_is_rejected(client):
    assert client.get("/learn/stats?window=0").status_code == 422
    assert client.get("/learn/stats?window=101").status_code == 422


# ── the store, directly ──────────────────────────────────────────────────────


def test_the_store_creates_its_schema_on_a_fresh_file(tmp_path):
    store = LearnStore(tmp_path / "nested" / "learn.db")
    try:
        assert (tmp_path / "nested" / "learn.db").exists()
        assert store.session_count() == 0
        assert store.recent_scores(5) == []
    finally:
        store.close()


def test_recent_scores_are_newest_first(store):
    for days_ago, score in enumerate([3, 5, 9]):
        seed_session(
            store, topic_id="intro_0", started_at=local_noon(days_ago), score=score
        )
    # created_at ordering, which is insertion order here.
    assert store.recent_scores(3) == [9, 5, 3]
    assert store.recent_scores(2) == [9, 5]


def test_last_seen_tracks_the_most_recent_draw(store):
    first = store.start_session("intro_0")
    second = store.start_session("intro_0")
    seen = store.last_seen()
    assert seen["intro_0"] >= first.started_at
    assert seen["intro_0"] == second.started_at


def test_recording_a_second_grade_raises(store):
    session = store.start_session("intro_0")
    kwargs = dict(
        score=8,
        feedback="f",
        missed_concepts=[],
        strengths=[],
        explanation="e",
        source="text",
        model="m",
        prompt_version="1",
        latency_ms=1,
    )
    store.record_grade(session.id, **kwargs)
    with pytest.raises(AlreadyGradedError):
        store.record_grade(session.id, **kwargs)


def test_the_store_survives_being_reopened(tmp_path):
    """It is a permanent record, not a cache — a restart must not lose it."""
    path = tmp_path / "learn.db"
    first = LearnStore(path)
    session = first.start_session("intro_0")
    first.record_grade(
        session.id,
        score=9,
        feedback="f",
        missed_concepts=[],
        strengths=[],
        explanation="e",
        source="text",
        model="m",
        prompt_version="1",
        latency_ms=1,
    )
    first.close()

    second = LearnStore(path)
    try:
        assert second.session_count() == 1
        assert second.recent_scores(5) == [9]
        assert second.get_session(session.id).topic_id == "intro_0"
    finally:
        second.close()
