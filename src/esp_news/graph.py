"""The LangGraph pipeline, assembled from nodes built and tested standalone.

    ingest -> dedup -> score -> curate -> summarize -> digest

Each node was runnable on its own first (esp-ingest, esp-dedup, esp-score,
esp-curate, esp-summarize); this only wires them together. Config that a node
needs but the state doesn't carry — feeds, the interest profile, thresholds —
is closed over here rather than stuffed into DigestState, which keeps the state
shape exactly what the plan specified.
"""

from __future__ import annotations

import logging
from datetime import date

from langgraph.graph import END, START, StateGraph

from esp_news.config import FeedsConfig
from esp_news.embeddings import EmbeddingClient
from esp_news.feedback import FeedbackStore
from esp_news.interests import InterestProfile
from esp_news.models import DigestState
from esp_news.nodes.curate import curate_articles
from esp_news.nodes.dedup import dedup_articles
from esp_news.nodes.digest import render_digest
from esp_news.nodes.ingest import ingest_articles
from esp_news.nodes.score import score_articles
from esp_news.nodes.summarize import summarize_articles
from esp_news.seen import SeenStore
from esp_news.summarize import Summarizer

logger = logging.getLogger(__name__)


def build_digest_graph(
    config: FeedsConfig,
    profile: InterestProfile,
    *,
    client: EmbeddingClient | None = None,
    seen: SeenStore | None = None,
    feedback: FeedbackStore | None = None,
    summarizer: Summarizer | None = None,
    use_llm_summary: bool = True,
    use_cached_feeds: bool = False,
    similarity_threshold: float = 0.6,
    top_n: int = 10,
    per_area_cap: int | None = 3,
    min_summary_chars: int = 1,
    wildcard: bool = True,
    when: date | None = None,
):
    """Compile the six-node digest graph. Returns a compiled LangGraph.

    ``use_llm_summary=False`` skips the Phase 9 fetch-and-summarize step and
    renders the RSS blurbs, which is what ``esp-digest --no-llm`` wants when
    the network is flaky or the run just needs to be free.

    ``use_cached_feeds=True`` serves ingest from the stored feed copies instead
    of the network — for tuning the interest profile, which changes only what
    happens downstream of ingest.

    ``feedback`` is the like/dislike store. Left as None the graph scores from
    ``interests.yaml`` alone, which is exactly what it did before verdicts
    existed.
    """

    def ingest_node(state: DigestState) -> dict:
        return {"raw_articles": ingest_articles(config, use_cached=use_cached_feeds)}

    def dedup_node(state: DigestState) -> dict:
        return {
            "deduped_articles": dedup_articles(
                state.raw_articles, similarity_threshold=similarity_threshold
            )
        }

    def score_node(state: DigestState) -> dict:
        return {
            "scored_articles": score_articles(
                state.deduped_articles,
                profile=profile,
                client=client,
                feedback=feedback,
            )
        }

    def curate_node(state: DigestState) -> dict:
        return {
            "curated_articles": curate_articles(
                state.scored_articles,
                top_n=top_n,
                per_area_cap=per_area_cap,
                seen=seen,
                min_summary_chars=min_summary_chars,
                wildcard=wildcard,
            )
        }

    def summarize_node(state: DigestState) -> dict:
        # Writes back to curated_articles rather than a new state key: the node
        # enriches the front page in place, and every downstream reader (digest,
        # the CLI, the API payload) already reads that list.
        return {
            "curated_articles": summarize_articles(
                state.curated_articles, summarizer=summarizer
            )
        }

    def digest_node(state: DigestState) -> dict:
        meta = {
            "scored_count": len(state.scored_articles),
            "feed_count": len(config.sources),
            "lookback_hours": config.settings.lookback_hours,
            "all_areas": [a.name for a in profile.areas],
        }
        return {
            "digest_markdown": render_digest(
                state.curated_articles, when=when, meta=meta
            )
        }

    graph = StateGraph(DigestState)
    graph.add_node("ingest", ingest_node)
    graph.add_node("dedup", dedup_node)
    graph.add_node("score", score_node)
    graph.add_node("curate", curate_node)
    graph.add_node("digest", digest_node)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "dedup")
    graph.add_edge("dedup", "score")
    graph.add_edge("score", "curate")

    if use_llm_summary:
        graph.add_node("summarize", summarize_node)
        graph.add_edge("curate", "summarize")
        graph.add_edge("summarize", "digest")
    else:
        graph.add_edge("curate", "digest")

    graph.add_edge("digest", END)

    return graph.compile()


def run_digest_graph(*args, **kwargs) -> DigestState:
    """Run the graph and hand back the final state as a DigestState.

    LangGraph returns the state as a plain dict when the schema is a Pydantic
    model, so re-wrap it rather than making every caller deal with both.
    """
    compiled = build_digest_graph(*args, **kwargs)
    result = compiled.invoke(DigestState())
    return result if isinstance(result, DigestState) else DigestState(**result)
