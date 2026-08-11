"""Load and validate topics.yaml — the topic bank and its draw weighting.

Same shape as ``config.py`` and ``interests.py``: one root-level YAML, read
into pydantic models that do the validating, so a malformed bank fails at load
with a usable message rather than halfway through a session.

``choose_topic`` lives here rather than in the router because it is about the
bank, and because keeping it a pure function of (bank, history, today, rng)
is what makes the weighting testable without a database or a clock.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

# topics.yaml lives at the repo root: src/esp_news/learn/topics.py -> parents[3].
# One level deeper than config.py and interests.py, which are not in a
# subpackage — the file sits beside feeds.yaml and interests.yaml regardless.
DEFAULT_TOPICS_PATH = Path(__file__).resolve().parents[3] / "topics.yaml"

Difficulty = Literal["intro", "intermediate", "advanced"]

DIFFICULTIES: tuple[Difficulty, ...] = ("intro", "intermediate", "advanced")


class DifficultyWeights(BaseModel):
    """Relative draw weight per difficulty. Not probabilities; they are
    normalised over whatever topics are actually eligible."""

    intro: float = 3.0
    intermediate: float = 2.0
    advanced: float = 1.0

    @field_validator("intro", "intermediate", "advanced")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("difficulty weights cannot be negative")
        return value

    def of(self, difficulty: str) -> float:
        return float(getattr(self, difficulty))


class LearnSettings(BaseModel):
    """The ``settings`` block of topics.yaml."""

    pass_score: int = 7
    timezone: str = ""
    weights: DifficultyWeights = Field(default_factory=DifficultyWeights)
    strong_weights: DifficultyWeights = Field(
        default_factory=lambda: DifficultyWeights(
            intro=1.0, intermediate=2.0, advanced=3.0
        )
    )
    strong_window: int = 5
    strong_average: float = 7.5
    repeat_cooldown_days: int = 14

    @field_validator("pass_score")
    @classmethod
    def _in_grade_range(cls, value: int) -> int:
        if not 1 <= value <= 10:
            raise ValueError("pass_score must be between 1 and 10")
        return value

    @field_validator("strong_window")
    @classmethod
    def _window_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("strong_window must be at least 1")
        return value

    def active_weights(self, recent_scores: Sequence[float]) -> DifficultyWeights:
        """Which weight set applies, given recent grades **newest first**.

        The window has to be full before the harder mix can apply. Promoting on
        two good sessions is how a bank of 78 topics turns into advanced-only
        after one lucky evening, and the way back down is another five sessions
        — by which point the tab is unpleasant enough to stop opening.
        """
        window = list(recent_scores)[: self.strong_window]
        if len(window) < self.strong_window:
            return self.weights
        if sum(window) / len(window) >= self.strong_average:
            return self.strong_weights
        return self.weights


class Topic(BaseModel):
    """One topic, plus the hint the grader scores an explanation against."""

    id: str
    name: str
    difficulty: Difficulty
    covers: str

    @field_validator("id", "name", "covers")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class TopicBank(BaseModel):
    settings: LearnSettings = Field(default_factory=LearnSettings)
    topics: list[Topic] = Field(default_factory=list)

    @field_validator("topics")
    @classmethod
    def _topics_usable(cls, topics: list[Topic]) -> list[Topic]:
        if not topics:
            raise ValueError("topics.yaml defines no topics — nothing to draw")
        ids = [t.id for t in topics]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            # Not cosmetic: ids are the foreign key in learn.db, so a duplicate
            # silently merges two topics' histories.
            raise ValueError(f"duplicate topic ids: {', '.join(sorted(dupes))}")
        return topics

    def get(self, topic_id: str) -> Topic | None:
        return next((t for t in self.topics if t.id == topic_id), None)


def load_topic_bank(path: str | Path | None = None) -> TopicBank:
    """Read topics.yaml into a :class:`TopicBank`."""
    path = Path(path) if path else DEFAULT_TOPICS_PATH
    if not path.exists():
        raise FileNotFoundError(f"topic bank not found: {path}")
    return TopicBank(**(yaml.safe_load(path.read_text()) or {}))


def choose_topic(
    bank: TopicBank,
    *,
    recent_scores: Sequence[float] = (),
    last_seen: dict[str, date] | None = None,
    today: date,
    rng: random.Random | None = None,
) -> Topic:
    """Draw a topic, weighted by difficulty and skipping recent ones.

    ``recent_scores`` is newest-first; ``last_seen`` maps topic id to the date
    it was last drawn. Both come from the store, but neither is looked up here
    — a pure function is the only version of this that can be tested for "does
    a good week actually change the mix" without a database in the way.

    The cooldown is a filter and the difficulty mix is a weighting, applied in
    that order. If the cooldown would leave nothing, it is dropped rather than
    raising: being told there is nothing to learn today is a worse outcome than
    an early repeat, and with a bank this size it only happens if the cooldown
    is set longer than the bank can cover.
    """
    rng = rng or random.Random()
    last_seen = last_seen or {}

    cutoff = today - timedelta(days=bank.settings.repeat_cooldown_days)
    eligible = [t for t in bank.topics if last_seen.get(t.id, date.min) <= cutoff]
    if not eligible:
        eligible = list(bank.topics)

    weights = bank.settings.active_weights(recent_scores)
    draw_weights = [weights.of(t.difficulty) for t in eligible]

    # All-zero weights would make random.choices raise. Falling back to uniform
    # keeps a bank whose weights were zeroed out by hand still usable.
    if sum(draw_weights) <= 0:
        return rng.choice(eligible)

    return rng.choices(eligible, weights=draw_weights, k=1)[0]
