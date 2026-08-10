"""Pydantic models for the news digest pipeline state."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# How much of an article's summary reaches the embedding model. Changing this
# changes every vector, so it invalidates the embedding cache *and* makes stored
# feedback text disagree with freshly-computed text — see `Article.embedding_text`.
EMBED_SUMMARY_CHARS = 1000


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
    content: str | None = None  # extracted full article text (Phase 9)
    long_summary: str | None = None  # LLM-written summary of `content` (Phase 9)
    # How the displayed summary was produced: "llm", "exempt", or "rss:<reason>".
    # Kept because the fallbacks are silent by design — this is the only way to
    # see that a source has quietly stopped being fetchable.
    summary_source: str = "rss"
    embedding: list[float] | None = None  # left unset: see nodes/score.py
    score: float | None = None  # best weighted area similarity (Phase 3)
    matched_area: str | None = None  # interest area that produced `score` (Phase 3)
    area_scores: dict[str, float] = Field(  # every area's weighted similarity
        default_factory=dict
    )
    # The same score with like/dislike feedback left out — what interests.yaml
    # alone said. Equal to `score` until there is feedback. Two things read it:
    # the wildcard draw, which must not be steerable by feedback, and the
    # digest's why-line, which shows the difference so a surprising pick can be
    # traced to a verdict rather than to the written profile.
    base_score: float | None = None
    # Drawn at random from the middle of the ranking — the exploration slot at
    # the end of the digest (Phase 4). Kept as a flag rather than inferred from
    # the score so everything downstream can tell "off-profile on purpose" from
    # "the front page was thin today".
    is_wildcard: bool = False

    @property
    def embedding_text(self) -> str:
        """The text that represents this article to the embedding model.

        Lives on the model rather than in the score node because two callers
        have to agree on it *byte for byte*: the scorer, which embeds it, and
        the feedback store, which records it so the vector can be recomputed
        years later. If they drifted apart, a stored verdict would describe a
        slightly different article than the one that was ranked, and the
        embedding cache would hold two entries where it should hold one.

        Title first and summaries truncated: the topical signal is in the
        opening, and feeds that dump full article text into the summary would
        otherwise dilute the vector.
        """
        return f"{self.title}\n\n{self.summary[:EMBED_SUMMARY_CHARS]}".strip()


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
