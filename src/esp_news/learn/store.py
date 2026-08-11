"""SQLite store for learning sessions, grades, and topic history.

Not JSONL, and the contrast with ``feedback.jsonl`` is the whole reason: a
verdict log is read by replaying it whole, but streaks are a query — "which
local days have a qualifying grade" — and answering that from an append-only
file means parsing every line on every stats request. The data is also small
and permanent, which is the case SQLite is for.

Connection handling follows ``embeddings.py``: one connection, opened with
``check_same_thread=False`` and guarded by a lock, in WAL mode. That matters
more here than there, because FastAPI runs sync endpoints in a threadpool, so
two requests genuinely can land on different threads.

**Which timestamp decides a session's day.** ``sessions.started_at``, not the
grade's. You did the work when you sat down: a session begun at 23:50 and
graded at 00:10 belongs to the night you did it, and anchoring on the grade
would silently move it to the next day and split a streak in two. Everything
is stored as ISO 8601 UTC and converted to a local calendar day exactly once,
in ``streaks.local_day``.

**One grade per session**, enforced by a unique index. A double-tapped submit
button must not count twice toward a streak, and a session that has already
been graded is a client bug rather than a re-grade. Re-running an improved
rubric over old explanations is a real want, but it is a different operation
than this one and would want its own table rather than a second row here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Beside the digests and feedback.jsonl rather than in .cache/, because unlike
# the caches this does not regenerate: src/esp_news/learn/store.py -> parents[3].
DEFAULT_LEARN_DB = Path(__file__).resolve().parents[3] / "learn.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics_seen (
    topic_id   TEXT PRIMARY KEY,
    last_seen  TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    topic_id   TEXT NOT NULL,
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    score           INTEGER NOT NULL,
    feedback        TEXT NOT NULL,
    missed_concepts TEXT NOT NULL,
    strengths       TEXT NOT NULL,
    explanation     TEXT NOT NULL,
    -- "text" today, "transcript" once speech lands. Stored so the two can be
    -- compared later rather than silently pooled.
    source          TEXT NOT NULL DEFAULT 'text',
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    latency_ms      INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS grades_one_per_session ON grades(session_id);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(stamp: str) -> datetime:
    """Read a stored timestamp. Naive values are UTC — see ``streaks``."""
    moment = datetime.fromisoformat(stamp)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Session:
    id: str
    topic_id: str
    started_at: datetime


@dataclass(frozen=True)
class GradeRow:
    """A grade as stored, with the session date it belongs to."""

    session_id: str
    topic_id: str
    score: int
    started_at: datetime
    created_at: datetime


class AlreadyGradedError(Exception):
    """Raised when a session that already has a grade is graded again."""


class LearnStore:
    def __init__(self, path: str | Path | None = DEFAULT_LEARN_DB) -> None:
        self.path = Path(path) if path else DEFAULT_LEARN_DB
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── writes ───────────────────────────────────────────────────────────────

    def start_session(self, topic_id: str) -> Session:
        """Open a session and record that the topic was drawn.

        The ``topics_seen`` bump happens here rather than at draw time on
        purpose: a topic you were shown and started is history, a topic the
        selector considered is not. Drawing without starting — refreshing for a
        different topic — must not put the discarded one on cooldown.
        """
        session = Session(uuid.uuid4().hex, topic_id, datetime.now(timezone.utc))
        started = session.started_at.isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions (id, topic_id, started_at) VALUES (?, ?, ?)",
                (session.id, topic_id, started),
            )
            self._conn.execute(
                """
                INSERT INTO topics_seen (topic_id, last_seen, times_seen)
                VALUES (?, ?, 1)
                ON CONFLICT(topic_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    times_seen = times_seen + 1
                """,
                (topic_id, started),
            )
        return session

    def record_grade(
        self,
        session_id: str,
        *,
        score: int,
        feedback: str,
        missed_concepts: list[str],
        strengths: list[str],
        explanation: str,
        source: str,
        model: str,
        prompt_version: str,
        latency_ms: int,
    ) -> None:
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO grades (
                        session_id, score, feedback, missed_concepts, strengths,
                        explanation, source, model, prompt_version, latency_ms,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        score,
                        feedback,
                        json.dumps(missed_concepts, ensure_ascii=False),
                        json.dumps(strengths, ensure_ascii=False),
                        explanation,
                        source,
                        model,
                        prompt_version,
                        latency_ms,
                        _now_iso(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AlreadyGradedError(
                    f"session {session_id} has already been graded"
                ) from exc

    # ── reads ────────────────────────────────────────────────────────────────

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, topic_id, started_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return Session(row["id"], row["topic_id"], _parse(row["started_at"]))

    def last_seen(self) -> dict[str, datetime]:
        """When each topic was last drawn, for the cooldown filter."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT topic_id, last_seen FROM topics_seen"
            ).fetchall()
        return {r["topic_id"]: _parse(r["last_seen"]) for r in rows}

    def recent_scores(self, limit: int) -> list[int]:
        """The most recent grades, newest first, for the difficulty weighting."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT score FROM grades ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [r["score"] for r in rows]

    def all_grades(self) -> list[GradeRow]:
        """Every grade with its session's start time, newest session first."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT g.session_id, g.score, g.created_at,
                       s.topic_id, s.started_at
                FROM grades g
                JOIN sessions s ON s.id = g.session_id
                ORDER BY s.started_at DESC
                """
            ).fetchall()
        return [
            GradeRow(
                session_id=r["session_id"],
                topic_id=r["topic_id"],
                score=r["score"],
                started_at=_parse(r["started_at"]),
                created_at=_parse(r["created_at"]),
            )
            for r in rows
        ]

    def session_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0]


def qualifying_days(
    grades: list[GradeRow], *, pass_score: int, to_day
) -> set[date]:
    """The set of local calendar days that contain a qualifying session.

    ``to_day`` converts a stored UTC instant to a local date — normally
    ``functools.partial(streaks.local_day, zone=...)``. Passed in rather than
    resolved here so this stays free of both the config and the clock.
    """
    return {to_day(g.started_at) for g in grades if g.score >= pass_score}
