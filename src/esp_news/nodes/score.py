"""Phase 3 — score node (the fitness function).

The core of the project. Each interest area from interests.yaml contributes a set
of reference vectors (its prose description plus each concrete phrase). Every
article's title+summary is embedded once, and scored against every reference:

    area_score    = max(cosine(article, ref) for ref in area) * area.weight
    article.score = max(area_score for area in profile)

Taking the max rather than an average means an article only has to be a strong
match for *one* thing I care about, which is how a front page actually works —
averaging would punish a great embedded-hardware post for having nothing to say
about Barcelona.
"""

from __future__ import annotations

import logging

import numpy as np
from langsmith import traceable

from esp_news.embeddings import EmbeddingClient
from esp_news.interests import InterestProfile, load_interests_profile
from esp_news.models import Article

logger = logging.getLogger(__name__)

# Summaries get truncated before embedding: the topical signal is in the opening,
# and feeds that dump full article text would otherwise dilute the vector.
_MAX_SUMMARY_CHARS = 1000


def _article_text(article: Article) -> str:
    """The text embedded for an article — title carries most of the signal."""
    return f"{article.title}\n\n{article.summary[:_MAX_SUMMARY_CHARS]}".strip()


@traceable(run_type="chain", name="score")
def score_articles(
    articles: list[Article],
    *,
    profile: InterestProfile | None = None,
    client: EmbeddingClient | None = None,
) -> list[Article]:
    """Attach ``score``, ``matched_area``, and ``area_scores`` to each article.

    Returns new ``Article`` objects in the input order — sorting and cutoffs are
    Phase 4's job. Embeddings are deliberately *not* stored on the returned
    articles: they'd bloat the pipeline state and every LangSmith trace payload
    with thousands of floats per article, and nothing downstream needs them.
    """
    if not articles:
        logger.info("No articles to score")
        return []

    profile = profile or load_interests_profile()
    client = client or EmbeddingClient(model=profile.embedding_model)

    # Flatten every area's reference texts into one matrix, remembering which
    # rows belong to which area so they can be collapsed back per area.
    reference_texts: list[str] = []
    area_rows: list[list[int]] = []
    for area in profile.areas:
        texts = area.reference_texts
        area_rows.append(list(range(len(reference_texts), len(reference_texts) + len(texts))))
        reference_texts.extend(texts)

    logger.info(
        "Scoring %d articles against %d interest areas (%d reference vectors, model=%s)",
        len(articles),
        len(profile.areas),
        len(reference_texts),
        profile.embedding_model,
    )

    profile_matrix = client.embed(reference_texts)  # (refs, dim)
    article_matrix = client.embed([_article_text(a) for a in articles])  # (n, dim)

    # Both sides are L2-normalized, so the dot product is cosine similarity.
    similarities = article_matrix @ profile_matrix.T  # (n, refs)

    # Collapse reference columns down to one weighted column per area.
    per_area = np.stack(
        [
            similarities[:, rows].max(axis=1) * area.weight
            for area, rows in zip(profile.areas, area_rows)
        ],
        axis=1,
    )  # (n, areas)

    best_area_idx = per_area.argmax(axis=1)
    scores = per_area.max(axis=1)
    area_names = [a.name for a in profile.areas]

    scored = [
        article.model_copy(
            update={
                "score": round(float(scores[i]), 4),
                "matched_area": area_names[best_area_idx[i]],
                "area_scores": {
                    name: round(float(per_area[i, j]), 4)
                    for j, name in enumerate(area_names)
                },
            }
        )
        for i, article in enumerate(articles)
    ]

    logger.info(
        "Scored %d articles (score range %.4f - %.4f, mean %.4f)",
        len(scored),
        scores.min(),
        scores.max(),
        scores.mean(),
    )
    return scored
