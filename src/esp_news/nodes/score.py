"""Phase 3 — score node (the fitness function).

The core of the project. Each interest area from interests.yaml contributes a set
of reference vectors (its prose description plus each concrete phrase). Every
article's title+summary is embedded once, and scored against every reference:

    positive      = max(cosine(article, ref))
    area_score    = max(0, positive - LAMBDA * max(cosine(article, avoid))) * weight
    article.score = max(area_score for area in profile)

Taking the max rather than an average means an article only has to be a strong
match for *one* thing I care about, which is how a front page actually works —
averaging would punish a great embedded-hardware post for having nothing to say
about Barcelona.

The `avoid` term is the only way the *written* profile can express a negative,
and it is deliberately the weakest thing in the file. Three properties keep it
from narrowing the digest:

  * It is per-area. Penalising an article under `florida` says nothing about its
    `spain` or `agentic_tooling` score, so the blast radius of a bad negative is
    one area rather than the whole profile.
  * It is clipped at zero, so a negative can push an area out of contention but
    can never drive an article's overall score below what some other area would
    have given it.
  * LAMBDA is small. A negative is a thumb on the scale between two articles that
    both already match the area, not a veto. Sized against a real corpus: see
    docs/interests-reasoning.md for the sweep and what each value did.

## Learned preference

Like/dislike verdicts (``feedback.py``) join the same arithmetic rather than
getting a mechanism of their own. Liked articles in an area are averaged into a
single extra reference vector; disliked ones become extra `avoid` vectors for
their area. Both attach to the article's `matched_area`, so the per-area blast
radius above covers a mislabelled verdict too.

Two things are deliberately different about the learned half:

  * **It is capped.** The learned reference can lift an area's score by at most
    LEARNED_MAX_SHIFT above what the written profile gave it, and the learned
    penalty can subtract at most the same. This is load-bearing, not a belt and
    braces: measured on a live corpus, one like on a Festa Major story would
    otherwise have promoted seven articles onto the front page from as deep as
    rank 97, which is the narrowing loop starting on day one.
  * **Its lambda is smaller.** An article-length negative is a sharper object
    than a hand-written phrase — it barely touches most of the corpus but hits
    its own neighbourhood at cosine 0.94, where a written phrase tops out at
    0.48. At the written LAMBDA that tail would be clipped flat by the cap and
    lose all proportionality, so the learned side gets its own smaller value and
    stays under the cap by construction.

The wildcard never sees any of this: `base_score` carries the written-profile-only
score forward, and `curate.py` draws its exploration slot from that ranking.
"""

from __future__ import annotations

import logging

import numpy as np
from langsmith import traceable

from esp_news.clients.embeddings import EmbeddingClient
from esp_news.storage.feedback import DISLIKE, LIKE, FeedbackStore
from esp_news.config.interests import InterestProfile, load_interests_profile
from esp_news.models import Article

logger = logging.getLogger(__name__)

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

# The ceiling on everything learned from like/dislike verdicts, in raw cosine
# units and applied before the area weight, same as the lambdas.
#
# 0.05, chosen against the live 271-article corpus rather than picked as a round
# number:
#
#   * Uncapped, a single like measured a lift of up to 0.158 and pulled articles
#     ranked 76, 89 and 97 onto the front page. Three times this cap.
#   * A lift of 0.05 brings ~20 more articles within reach of the rank-10 cutoff;
#     0.10 brings 75, and 0.15 brings 136 — half the corpus, at which point the
#     verdicts are ranking the page rather than nudging it.
#   * The front page's own score range top to bottom is 0.11 (0.672 down to
#     0.562). Keeping the cap under half of that is the concrete version of
#     "feedback reorders the page and pulls in near-misses; interests.yaml still
#     decides what the page is about".
#
# Raise it only with the same measurement in hand, and expect the digest to
# narrow when you do.
LEARNED_MAX_SHIFT = 0.05

# The `avoid` lambda for disliked *articles*, as opposed to written phrases.
#
# The written 0.15 was sized against phrases like "Jacksonville weather alert",
# which peak at 0.477 against this corpus. Article-length text behaves
# differently: it ignores most of the corpus (median similarity 0.115) and then
# hits its own neighbourhood at up to 0.939 — the same-story-different-outlet
# case, which dedup does not catch across sources. At 0.15 that tail would ask
# for a 0.141 penalty, get clipped to LEARNED_MAX_SHIFT, and flatten into "-0.05
# for anything vaguely similar", losing the difference between a near-duplicate
# and a cousin.
#
# 0.05 keeps the response proportional and under the cap by construction, since
# cosine cannot exceed 1. A near-duplicate at 0.94 pays 0.047 — enough to move an
# article from the edge of the page to about rank 29 — while a same-topic
# neighbour at 0.30 pays 0.015. It is deliberately weaker than the written
# lambda: a phrase you sat down and wrote outranks a single tap on a screen.
LEARNED_AVOID_LAMBDA = 0.05


@traceable(run_type="chain", name="score")
def score_articles(
    articles: list[Article],
    *,
    profile: InterestProfile | None = None,
    client: EmbeddingClient | None = None,
    avoid_lambda: float = DEFAULT_AVOID_LAMBDA,
    feedback: FeedbackStore | None = None,
    learned_lambda: float = LEARNED_AVOID_LAMBDA,
    max_shift: float = LEARNED_MAX_SHIFT,
) -> list[Article]:
    """Attach ``score``, ``base_score``, ``matched_area`` and ``area_scores``.

    Returns new ``Article`` objects in the input order — sorting and cutoffs are
    Phase 4's job. Embeddings are deliberately *not* stored on the returned
    articles: they'd bloat the pipeline state and every LangSmith trace payload
    with thousands of floats per article, and nothing downstream needs them.

    ``avoid_lambda`` scales the penalty from each area's ``avoid`` list; pass 0
    to score positives only, which is what every area without an ``avoid`` list
    does anyway.

    ``feedback`` folds like/dislike verdicts into the areas they were recorded
    against. Pass None — or a store with nothing in it — and every number this
    returns is bit-for-bit what it was before the mechanism existed, including
    ``base_score``, which is then just a copy of ``score``.
    """
    if not articles:
        logger.info("No articles to score")
        return []

    profile = profile or load_interests_profile()
    client = client or EmbeddingClient(model=profile.embedding_model)

    liked = feedback.texts_by_area(LIKE) if feedback else {}
    disliked = feedback.texts_by_area(DISLIKE) if feedback else {}

    # Everything on the profile side goes into one list, and therefore one embed
    # call: written references, written avoid phrases, and the text of every
    # liked and disliked article. Row ranges are tracked per area so the flat
    # matrix can be collapsed back afterwards.
    #
    # The single call is not tidiness. `EmbeddingClient` sweeps expired vectors
    # at the end of a call, *after* that call's lookups have refreshed what they
    # touched — so a long-lived vector embedded in the first call is safe, and
    # one embedded in a later call can be swept and immediately re-bought. These
    # are exactly the vectors that have to survive between runs.
    profile_texts: list[str] = []
    area_rows: list[list[int]] = []
    avoid_rows: list[list[int]] = []
    like_rows: list[list[int]] = []
    dislike_rows: list[list[int]] = []

    def _claim(texts: list[str]) -> list[int]:
        start = len(profile_texts)
        profile_texts.extend(texts)
        return list(range(start, len(profile_texts)))

    for area in profile.areas:
        area_rows.append(_claim(area.reference_texts))
        avoid_rows.append(_claim(area.avoid_texts if avoid_lambda else []))
        like_rows.append(_claim(liked.get(area.name, [])))
        dislike_rows.append(_claim(disliked.get(area.name, []) if learned_lambda else []))

    learned = any(like_rows) or any(dislike_rows)

    logger.info(
        "Scoring %d articles against %d interest areas "
        "(%d reference vectors, %d avoid vectors at lambda=%.2f, model=%s)",
        len(articles),
        len(profile.areas),
        sum(len(r) for r in area_rows),
        sum(len(r) for r in avoid_rows),
        avoid_lambda,
        profile.embedding_model,
    )
    if learned:
        logger.info(
            "Feedback: %d liked and %d disliked article(s) in play "
            "(learned lambda=%.2f, capped at %.2f before weight)",
            sum(len(r) for r in like_rows),
            sum(len(r) for r in dislike_rows),
            learned_lambda,
            max_shift,
        )

    profile_matrix = client.embed(profile_texts)  # (refs+avoids+likes+dislikes, dim)
    article_matrix = client.embed([a.embedding_text for a in articles])  # (n, dim)

    # Both sides are L2-normalized, so the dot product is cosine similarity.
    similarities = article_matrix @ profile_matrix.T  # (n, profile_texts)

    def _centroid(rows: list[int]) -> np.ndarray:
        """One vector standing in for every article liked in an area.

        Averaging rather than keeping each like as its own reference, per the
        design note: at this many labels a learned model fits noise, and a mean
        is the whole of the honest version of it.

        Worth being clear about what this does and does not buy, because the
        intuition is wrong. Measured on the live corpus, the centroid's peak
        similarity to the rest of the corpus *rises* as likes accumulate (0.445
        at one like, 0.630 at eight) — averaging turns specific articles into a
        generic "article of this kind", which matches more of the area, not
        less. So it broadens rather than narrows, which is what we want, but it
        is emphatically not what stops a single like dominating. The cap is.
        """
        centroid = profile_matrix[rows].mean(axis=0)
        return centroid / max(float(np.linalg.norm(centroid)), 1e-12)

    def _column(
        area, rows: list[int], neg_rows: list[int], pos_rows: list[int],
        dis_rows: list[int], *, use_learned: bool
    ) -> np.ndarray:
        """One area's contribution: best reference, less its worst offence."""
        best = similarities[:, rows].max(axis=1)

        if use_learned and pos_rows:
            # Capped as a *lift over the written profile*, not as an absolute
            # ceiling: an area the profile already scores highly is not held
            # back, and an area it scores at zero cannot be talked into the
            # front page by verdicts alone.
            learned_best = article_matrix @ _centroid(pos_rows)
            best = np.minimum(np.maximum(best, learned_best), best + max_shift)

        penalty = np.zeros_like(best)
        if neg_rows:
            penalty = penalty + avoid_lambda * similarities[:, neg_rows].max(axis=1)
        if use_learned and dis_rows:
            penalty = penalty + np.minimum(
                learned_lambda * similarities[:, dis_rows].max(axis=1), max_shift
            )

        # Clipped at zero: an avoid can cost an area the article, but it must
        # never drag the article's overall score below another area's claim.
        return np.maximum(best - penalty, 0.0) * area.weight

    def _stack(*, use_learned: bool) -> np.ndarray:
        """Collapse reference columns down to one weighted column per area."""
        return np.stack(
            [
                _column(area, rows, neg, pos, dis, use_learned=use_learned)
                for area, rows, neg, pos, dis in zip(
                    profile.areas, area_rows, avoid_rows, like_rows, dislike_rows
                )
            ],
            axis=1,
        )  # (n, areas)

    per_area = _stack(use_learned=True)
    # What interests.yaml alone said. Computed only when there is feedback to
    # leave out; with none, the two are the same array by construction rather
    # than by luck, which is what makes the cold-start guarantee a fact about
    # the code instead of a claim about floating point.
    per_area_written = _stack(use_learned=False) if learned else per_area

    best_area_idx = per_area.argmax(axis=1)
    scores = per_area.max(axis=1)
    base_scores = per_area_written.max(axis=1)
    area_names = [a.name for a in profile.areas]

    scored = [
        article.model_copy(
            update={
                "score": round(float(scores[i]), 4),
                "base_score": round(float(base_scores[i]), 4),
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
