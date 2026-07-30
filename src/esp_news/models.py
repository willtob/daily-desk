"""Pydantic models for the news digest pipeline state."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Article(BaseModel):
    """A single news article pulled from an RSS feed.

    Fields beyond the Phase 1 basics (embedding, score, content) are declared
    here with defaults so later phases can fill them in without changing the
    model.
    """

    title: str
    url: str
    source: str  # human-readable feed name, e.g. "Hackaday"
    theme: str  # feed theme tag, e.g. "embedded_wearables"
    published: datetime | None = None
    summary: str = ""  # raw RSS summary/description (HTML stripped in Phase 2)
    content: str | None = None  # optional full-text (future)
    embedding: list[float] | None = None  # left unset: see nodes/score.py
    score: float | None = None  # best weighted area similarity (Phase 3)
    matched_area: str | None = None  # interest area that produced `score` (Phase 3)
    area_scores: dict[str, float] = Field(  # every area's weighted similarity
        default_factory=dict
    )


class DigestState(BaseModel):
    """Full pipeline state threaded through the LangGraph graph.

    Only ``raw_articles`` is populated in Phase 1 (ingest); later phases fill
    the rest. Defined now so the end-to-end state shape lives in one place.
    """

    raw_articles: list[Article] = Field(default_factory=list)
    deduped_articles: list[Article] = Field(default_factory=list)
    scored_articles: list[Article] = Field(default_factory=list)
    curated_articles: list[Article] = Field(default_factory=list)
    digest_markdown: str | None = None
