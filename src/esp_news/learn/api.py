"""Phase L — the 15-minute learning endpoints.

An ``APIRouter`` rather than a second app: the ESP32, the desktop widget and
this all want one base URL, and ``esp-serve`` is already the thing that runs.
Mounted in ``esp_news.api``.

    GET  /learn/topic          draw a topic to explain
    POST /learn/session/start  open a session (starts the 15 minutes)
    POST /learn/grade          submit an explanation, get a structured grade
    GET  /learn/stats          streak, sessions, rolling average

The dependencies are ``lru_cache``d singletons so the topic bank is parsed once
and the SQLite connection is opened once, and so tests can replace them through
``app.dependency_overrides`` without touching the real learn.db.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from openai import APITimeoutError, OpenAIError
from pydantic import BaseModel, Field

from esp_news.clients.embeddings import MissingAPIKeyError
from esp_news.learn.grade import Grade, Grader
from esp_news.learn.store import AlreadyGradedError, LearnStore, qualifying_days
from esp_news.learn.streaks import current_streak, local_day, longest_streak, resolve_zone, today_in
from esp_news.learn.topics import TopicBank, choose_topic, load_topic_bank

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/learn", tags=["learn"])


@lru_cache(maxsize=1)
def get_bank() -> TopicBank:
    return load_topic_bank()


@lru_cache(maxsize=1)
def get_store() -> LearnStore:
    return LearnStore()


@lru_cache(maxsize=1)
def get_grader() -> Grader:
    return Grader()


# ── request/response models ──────────────────────────────────────────────────


class TopicOut(BaseModel):
    """A drawn topic.

    ``covers`` is deliberately absent. It is the grading checklist, and showing
    it before the explanation turns a recall exercise into a reading exercise —
    the one thing this feature exists not to be.
    """

    topic_id: str
    name: str
    difficulty: str


class SessionStartIn(BaseModel):
    topic_id: str


class SessionOut(BaseModel):
    session_id: str
    topic_id: str
    started_at: datetime


class GradeIn(BaseModel):
    session_id: str
    explanation_text: str
    # "transcript" is accepted now so that speech input is a value change rather
    # than a schema change. Both are graded identically — see Grader.grade.
    source: Literal["text", "transcript"] = "text"


class GradeOut(Grade):
    """The grade, plus whether it counted toward the streak."""

    session_id: str
    counted: bool
    pass_score: int


class StatsOut(BaseModel):
    current_streak: int
    longest_streak: int
    sessions_completed: int
    rolling_average: float | None = None
    average_all_time: float | None = None
    rolling_window: int
    pass_score: int
    last_session_at: datetime | None = None


# ── endpoints ────────────────────────────────────────────────────────────────


@router.get("/topic", response_model=TopicOut)
def draw_topic(
    bank: TopicBank = Depends(get_bank),
    store: LearnStore = Depends(get_store),
) -> TopicOut:
    """Draw a topic, weighted by difficulty and skipping recently-seen ones.

    Drawing does not start anything. Refreshing for a different topic is free,
    and the discarded one is not put on cooldown — only ``/session/start``
    records that a topic was seen.
    """
    zone = resolve_zone(bank.settings.timezone)
    seen = {tid: local_day(when, zone) for tid, when in store.last_seen().items()}

    topic = choose_topic(
        bank,
        recent_scores=store.recent_scores(bank.settings.strong_window),
        last_seen=seen,
        today=today_in(zone),
    )
    return TopicOut(topic_id=topic.id, name=topic.name, difficulty=topic.difficulty)


@router.post("/session/start", response_model=SessionOut, status_code=201)
def start_session(
    body: SessionStartIn,
    bank: TopicBank = Depends(get_bank),
    store: LearnStore = Depends(get_store),
) -> SessionOut:
    """Start the fifteen minutes. ``started_at`` is UTC; the client runs the timer."""
    if bank.get(body.topic_id) is None:
        raise HTTPException(
            status_code=404, detail=f"unknown topic: {body.topic_id!r}"
        )

    session = store.start_session(body.topic_id)
    return SessionOut(
        session_id=session.id,
        topic_id=session.topic_id,
        started_at=session.started_at,
    )


@router.post("/grade", response_model=GradeOut)
def grade_explanation(
    body: GradeIn,
    bank: TopicBank = Depends(get_bank),
    store: LearnStore = Depends(get_store),
    grader: Grader = Depends(get_grader),
) -> GradeOut:
    """Grade an explanation and record it.

    The session is not checked against the fifteen-minute window. Going over is
    between me and the timer; a server that refused a late submission would
    throw away the explanation I just spent the time writing.
    """
    session = store.get_session(body.session_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"unknown session: {body.session_id!r}"
        )

    topic = bank.get(session.topic_id)
    if topic is None:
        # The topic was removed or renamed in topics.yaml after this session
        # started. Nothing to grade against, and inventing a checklist would be
        # worse than saying so.
        raise HTTPException(
            status_code=409,
            detail=f"topic {session.topic_id!r} is no longer in topics.yaml",
        )

    try:
        result = grader.grade(topic, body.explanation_text, source=body.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except MissingAPIKeyError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except APITimeoutError:
        raise HTTPException(status_code=504, detail="Grading timed out — try again.")
    except OpenAIError as exc:
        logger.warning("Grading failed for %s: %s", topic.id, exc)
        raise HTTPException(status_code=502, detail=f"Grading failed: {exc}")

    grade = result.grade
    try:
        store.record_grade(
            body.session_id,
            score=grade.score,
            feedback=grade.feedback,
            missed_concepts=grade.missed_concepts,
            strengths=grade.strengths,
            explanation=body.explanation_text,
            source=body.source,
            model=result.model,
            prompt_version=result.prompt_version,
            latency_ms=result.latency_ms,
        )
    except AlreadyGradedError as exc:
        # Paid for and thrown away, but the alternative is a double-tapped
        # submit adding two qualifying sessions to one day's streak.
        raise HTTPException(status_code=409, detail=str(exc))

    pass_score = bank.settings.pass_score
    return GradeOut(
        **grade.model_dump(),
        session_id=body.session_id,
        counted=grade.score >= pass_score,
        pass_score=pass_score,
    )


@router.get("/stats", response_model=StatsOut)
def stats(
    window: int = Query(10, ge=1, le=100, description="Sessions in the rolling average."),
    bank: TopicBank = Depends(get_bank),
    store: LearnStore = Depends(get_store),
) -> StatsOut:
    """Streaks and averages.

    Streaks are counted over the local calendar days of *qualifying* sessions —
    a session graded below ``pass_score`` happened, and counts toward
    ``sessions_completed`` and the averages, but does not hold the streak up.
    """
    zone = resolve_zone(bank.settings.timezone)
    grades = store.all_grades()

    days = qualifying_days(
        grades,
        pass_score=bank.settings.pass_score,
        to_day=lambda moment: local_day(moment, zone),
    )

    scores = [g.score for g in grades]  # newest session first
    recent = scores[:window]

    return StatsOut(
        current_streak=current_streak(days, today=today_in(zone)),
        longest_streak=longest_streak(days),
        sessions_completed=len(grades),
        rolling_average=round(sum(recent) / len(recent), 2) if recent else None,
        average_all_time=round(sum(scores) / len(scores), 2) if scores else None,
        rolling_window=window,
        pass_score=bank.settings.pass_score,
        last_session_at=grades[0].started_at if grades else None,
    )
