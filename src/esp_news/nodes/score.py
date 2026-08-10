"""Phase 3 — score node (the fitness function).

The core of the project. Each interest area from interests.yaml contributes a set
of reference vectors (its prose description plus each concrete phrase). Every
article's title+summary is embedded once, and scored against every reference:

    area_score    = max(0, max(cosine(article, ref)) - LAMBDA * max(cosine(article, avoid))) * weight
    article.score = max(area_score for area in profile)

Taking the max rather than an average means an article only has to be a strong
match for *one* thing I care about, which is how a front page actually works —
averaging would punish a great embedded-hardware post for having nothing to say
about Barcelona.

The `avoid` term is the only way the profile can express a negative, and it is
deliberately the weakest thing in the file. Three properties keep it from
narrowing the digest:

  * It is per-area. Penalising an article under `florida` says nothing about its
    `spain` or `agentic_tooling` score, so the blast radius of a bad negative is
    one area rather than the whole profile.
  * It is clipped at zero, so a negative can push an area out of contention but
    can never drive an article's overall score below what some other area would
    have given it.
  * LAMBDA is small. A negative is a thumb on the scale between two articles that
    both already match the area, not a veto. Sized against a real corpus: see
    docs/interests-reasoning.md for the sweep and what each value did.
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

# How hard an `avoid:` match pulls an area's score down, in raw cosine units and
# applied before the area weight, so the same lambda means the same thing in
# every area regardless of how that area is weighted.
#
# 0.15 is the *smallest* value that does the job, which is the property worth
# having here. Swept against a live 173-article corpus: it is already enough to
# push a Jacksonville thunderstorm alert off the front page, while 0.35 starts
# demoting "Atlantic hurricane season ramps up: what South Florida residents
# need to know" below a routine local shooting — collateral, because that story
# also looks like a Florida weather alert to the embedding. The sweep is in
# docs/interests-reasoning.md. Raise this only with the same evidence in hand.
DEFAULT_AVOID_LAMBDA = 0.15


def _article_text(article: Article) -> str:
    """The text embedded for an article — title carries most of the signal."""
    return f"{article.title}\n\n{article.summary[:_MAX_SUMMARY_CHARS]}".strip()


@traceable(run_type="chain", name="score")
def score_articles(
    articles: list[Article],
    *,
    profile: InterestProfile | None = None,
    client: EmbeddingClient | None = None,
    avoid_lambda: float = DEFAULT_AVOID_LAMBDA,
) -> list[Article]:
    """Attach ``score``, ``matched_area``, and ``area_scores`` to each article.

    Returns new ``Article`` objects in the input order — sorting and cutoffs are
    Phase 4's job. Embeddings are deliberately *not* stored on the returned
    articles: they'd bloat the pipeline state and every LangSmith trace payload
    with thousands of floats per article, and nothing downstream needs them.

    ``avoid_lambda`` scales the penalty from each area's ``avoid`` list; pass 0
    to score positives only, which is what every area without an ``avoid`` list
    does anyway.
    """
    if not articles:
        logger.info("No articles to score")
        return []

    profile = profile or load_interests_profile()
    client = client or EmbeddingClient(model=profile.embedding_model)

    # Flatten every area's reference texts into one matrix, remembering which
    # rows belong to which area so they can be collapsed back per area. The
    # avoid phrases get the same treatment in a second matrix — one embed call
    # for both would work, but keeping them apart means an area with no avoid
    # list takes a code path with no penalty term in it at all.
    reference_texts: list[str] = []
    area_rows: list[list[int]] = []
    avoid_texts: list[str] = []
    avoid_rows: list[list[int]] = []
    for area in profile.areas:
        texts = area.reference_texts
        area_rows.append(list(range(len(reference_texts), len(reference_texts) + len(texts))))
        reference_texts.extend(texts)

        negatives = area.avoid_texts if avoid_lambda else []
        avoid_rows.append(list(range(len(avoid_texts), len(avoid_texts) + len(negatives))))
        avoid_texts.extend(negatives)

    logger.info(
        "Scoring %d articles against %d interest areas "
        "(%d reference vectors, %d avoid vectors at lambda=%.2f, model=%s)",
        len(articles),
        len(profile.areas),
        len(reference_texts),
        len(avoid_texts),
        avoid_lambda,
        profile.embedding_model,
    )

    profile_matrix = client.embed(reference_texts)  # (refs, dim)
    article_matrix = client.embed([_article_text(a) for a in articles])  # (n, dim)

    # Both sides are L2-normalized, so the dot product is cosine similarity.
    similarities = article_matrix @ profile_matrix.T  # (n, refs)
    avoid_similarities = (
        article_matrix @ client.embed(avoid_texts).T if avoid_texts else None
    )  # (n, avoids) or None

    def _column(area, rows: list[int], neg_rows: list[int]) -> np.ndarray:
        """One area's contribution: best reference, less its worst offence."""
        best = similarities[:, rows].max(axis=1)
        if neg_rows and avoid_similarities is not None:
            worst = avoid_similarities[:, neg_rows].max(axis=1)
            # Clipped at zero: an avoid can cost an area the article, but it must
            # never drag the article's overall score below another area's claim.
            best = np.maximum(best - avoid_lambda * worst, 0.0)
        return best * area.weight

    # Collapse reference columns down to one weighted column per area.
    per_area = np.stack(
        [
            _column(area, rows, neg_rows)
            for area, rows, neg_rows in zip(profile.areas, area_rows, avoid_rows)
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
