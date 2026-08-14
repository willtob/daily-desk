"""Tests for what like/dislike verdicts are allowed to do to a score.

The mechanism's whole risk is a narrowing loop: liking things makes more of them
appear, which gets liked, and a month later the digest is one topic. So most of
this file is about limits rather than about the feature working — the cap, the
per-area containment, and the guarantee that with no verdicts recorded the
ranking is the one interests.yaml produced on its own.

Embeddings are faked, as in ``test_scoring.py``: the arithmetic under test is
ours, and real vectors would turn these into a measurement of OpenAI.
"""

from __future__ import annotations

import pytest

from esp_news.storage.feedback import DISLIKE, LIKE, FeedbackStore
from esp_news.config.interests import InterestArea, InterestProfile
from esp_news.models import Article
from esp_news.nodes.score import (
    LEARNED_AVOID_LAMBDA,
    LEARNED_MAX_SHIFT,
    score_articles,
)
from tests.test_scoring import FakeEmbeddings, article


@pytest.fixture
def store(tmp_path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.jsonl")


def profile(*, avoid: list[str] | None = None, weight: float = 1.0) -> InterestProfile:
    return InterestProfile(
        areas=[
            InterestArea(
                name="local",
                description="local news",
                avoid=avoid or [],
                weight=weight,
            ),
            InterestArea(name="tech", description="tech news", weight=weight),
        ]
    )


# A basis in which similarity can be dialled by hand.
#
# FLAVOUR is the shape that matters: an article the written profile recognises
# only partly (cosine 0.6 to the reference) but which is exactly what a liked
# article looked like. That gap is where the learned signal lives, and the whole
# question is how much of it the cap lets through.
LOCAL = [1.0, 0.0, 0.0, 0.0]       # the written reference
TECH = [0.0, 1.0, 0.0, 0.0]
FLAVOUR = [0.6, 0.0, 0.8, 0.0]     # cosine 0.6 to LOCAL, unit length already
FAR = [0.0, 0.0, 0.0, 1.0]


def table(**extra) -> dict[str, list[float]]:
    base = {"local news": LOCAL, "tech news": TECH}
    base.update(extra)
    return base


# ── cold start ───────────────────────────────────────────────────────────────


def test_with_no_verdicts_the_ranking_is_byte_identical(store):
    """The guarantee the whole feature is allowed to exist under.

    Not "close enough" — the same floats. Scoring runs the learned branch only
    when there is something learned, so this is a property of the code path
    rather than of floating-point luck.
    """
    articles = [article("one"), article("two"), article("three")]
    lookup = table(
        **{a.embedding_text: v for a, v in zip(articles, (LOCAL, FLAVOUR, FAR))}
    )

    without = score_articles(
        articles, profile=profile(), client=FakeEmbeddings(lookup)
    )
    with_empty = score_articles(
        articles, profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )

    assert [a.score for a in without] == [a.score for a in with_empty]
    assert [a.matched_area for a in without] == [a.matched_area for a in with_empty]
    assert [a.area_scores for a in without] == [a.area_scores for a in with_empty]


def test_base_score_equals_score_when_there_is_no_feedback(store):
    """So anything reading base_score behaves identically on a cold start."""
    articles = [article("one"), article("two")]
    lookup = table(**{a.embedding_text: v for a, v in zip(articles, (LOCAL, FAR))})

    scored = score_articles(
        articles, profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )
    assert all(a.base_score == a.score for a in scored)


# ── the cap ──────────────────────────────────────────────────────────────────


def test_a_like_cannot_lift_a_score_by_more_than_the_cap(store):
    """Measured uncapped on a live corpus, one like lifted a near neighbour by
    0.158 and pulled rank 97 onto the front page. This is the bound on that."""
    liked, neighbour = article("liked story"), article("same topic")
    store.record(LIKE, url=liked.url, text=liked.embedding_text, matched_area="local")

    lookup = table(**{liked.embedding_text: FLAVOUR, neighbour.embedding_text: FLAVOUR})
    scored = score_articles(
        [neighbour], profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )

    lift = scored[0].score - scored[0].base_score
    assert lift == pytest.approx(LEARNED_MAX_SHIFT, abs=1e-4)


def test_the_cap_scales_with_the_area_weight_like_the_lambdas_do(store):
    """It is applied before the weight, so a 1.2x area moves 1.2x as far."""
    liked, neighbour = article("liked story"), article("same topic")
    store.record(LIKE, url=liked.url, text=liked.embedding_text, matched_area="local")

    lookup = table(**{liked.embedding_text: FLAVOUR, neighbour.embedding_text: FLAVOUR})
    scored = score_articles(
        [neighbour],
        profile=profile(weight=1.2),
        client=FakeEmbeddings(lookup),
        feedback=store,
    )

    lift = scored[0].score - scored[0].base_score
    assert lift == pytest.approx(LEARNED_MAX_SHIFT * 1.2, abs=1e-4)


def test_a_like_never_lowers_a_score(store):
    """The learned reference is a floor-raiser only.

    An article the written profile likes and the centroid does not must keep the
    written score — otherwise a verdict could quietly demote something nobody
    voted against.
    """
    liked, other = article("liked story"), article("written favourite")
    store.record(LIKE, url=liked.url, text=liked.embedding_text, matched_area="local")

    lookup = table(**{liked.embedding_text: FAR, other.embedding_text: LOCAL})
    scored = score_articles(
        [other], profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )

    assert scored[0].score == scored[0].base_score


def test_likes_cannot_manufacture_a_front_page_out_of_nothing(store):
    """The cap is a lift over the written profile, not an absolute score.

    An article the profile scores near zero cannot be talked onto the page by
    verdicts — the most it can gain is the cap.
    """
    liked, stranger = article("liked story"), article("unrelated")
    store.record(LIKE, url=liked.url, text=liked.embedding_text, matched_area="local")

    lookup = table(**{liked.embedding_text: FAR, stranger.embedding_text: FAR})
    scored = score_articles(
        [stranger], profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )

    assert scored[0].score <= scored[0].base_score + LEARNED_MAX_SHIFT + 1e-9


# ── containment ──────────────────────────────────────────────────────────────


def test_a_verdict_only_moves_the_area_it_was_recorded_against(store):
    """The same reasoning as the written `avoid:` list: a mislabelled verdict
    costs one area, not the profile."""
    liked, target = article("liked story"), article("target")
    store.record(LIKE, url=liked.url, text=liked.embedding_text, matched_area="local")

    lookup = table(**{liked.embedding_text: FLAVOUR, target.embedding_text: FLAVOUR})
    scored = score_articles(
        [target], profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )

    without = score_articles([target], profile=profile(), client=FakeEmbeddings(lookup))
    assert scored[0].area_scores["tech"] == without[0].area_scores["tech"]
    assert scored[0].area_scores["local"] > without[0].area_scores["local"]


def test_a_verdict_in_an_area_that_no_longer_exists_is_ignored(store):
    """Renaming an area in interests.yaml must not break scoring."""
    liked = article("liked story")
    store.record(LIKE, url=liked.url, text=liked.embedding_text, matched_area="gone")

    target = article("target")
    lookup = table(**{target.embedding_text: FLAVOUR})
    scored = score_articles(
        [target], profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )

    assert scored[0].score == scored[0].base_score


# ── dislikes ─────────────────────────────────────────────────────────────────


def test_a_dislike_penalises_its_own_neighbourhood(store):
    disliked, neighbour = article("disliked story"), article("same topic")
    store.record(
        DISLIKE, url=disliked.url, text=disliked.embedding_text, matched_area="local"
    )

    lookup = table(**{disliked.embedding_text: FLAVOUR, neighbour.embedding_text: FLAVOUR})
    scored = score_articles(
        [neighbour], profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )

    # The neighbour *is* the disliked article's shape, so it matches the negative
    # at cosine 1.0 and pays the full learned lambda off a written score of 0.6.
    expected = 0.6 - LEARNED_AVOID_LAMBDA
    assert scored[0].score == pytest.approx(expected, abs=1e-3)
    assert scored[0].score < scored[0].base_score


def test_the_learned_penalty_is_under_the_cap_by_construction():
    """Cosine tops out at 1, so the lambda alone bounds the penalty.

    This is the property that lets the learned dislike stay proportional instead
    of being clipped flat: if a future change raises the lambda past the cap,
    every strong match starts paying the same penalty and the difference between
    a near-duplicate and a cousin disappears.
    """
    assert 0 < LEARNED_AVOID_LAMBDA <= LEARNED_MAX_SHIFT


def test_learned_dislikes_are_weaker_than_written_ones():
    """A phrase you sat down and wrote outranks a single tap on a screen."""
    from esp_news.nodes.score import DEFAULT_AVOID_LAMBDA

    assert LEARNED_AVOID_LAMBDA < DEFAULT_AVOID_LAMBDA


def test_a_dislike_cannot_push_a_score_below_zero(store):
    """Same clipping the written avoid list gets, for the same reason."""
    disliked = article("disliked story")
    store.record(
        DISLIKE, url=disliked.url, text=disliked.embedding_text, matched_area="local"
    )

    lookup = table(**{disliked.embedding_text: LOCAL})
    scored = score_articles(
        [disliked],
        profile=profile(),
        client=FakeEmbeddings(lookup),
        feedback=store,
        learned_lambda=5.0,   # absurd on purpose
    )

    assert scored[0].score >= 0.0


# ── likes are averaged, not accumulated ──────────────────────────────────────


def test_many_likes_in_an_area_stay_one_reference_vector(store):
    """Averaging is what stops a well-liked area outgrowing the written profile.

    Ten likes must not become ten chances to match — the area gets one learned
    reference however many verdicts feed it, and the cap bounds that one.
    """
    likes = [article(f"liked-{i}") for i in range(10)]
    lookup = table()
    for i, art in enumerate(likes):
        # Spread them around LOCAL so the centroid is not any one of them.
        vector = [1.0, 0.0, 0.1 * i, 0.0]
        lookup[art.embedding_text] = vector
        store.record(LIKE, url=art.url, text=art.embedding_text, matched_area="local")

    target = article("target")
    lookup[target.embedding_text] = FLAVOUR

    scored = score_articles(
        [target], profile=profile(), client=FakeEmbeddings(lookup), feedback=store
    )
    assert scored[0].score - scored[0].base_score <= LEARNED_MAX_SHIFT + 1e-9


def test_the_scorer_embeds_every_feedback_text_each_run(store):
    """What keeps those vectors alive in the cache, which evicts on last-used.

    They also have to be in the *first* embed call: the client sweeps expired
    vectors at the end of a call, after that call's lookups have refreshed what
    they touched, so a long-lived vector embedded later could be swept and
    immediately re-bought.
    """
    liked, disliked = article("liked"), article("disliked")
    store.record(LIKE, url=liked.url, text=liked.embedding_text, matched_area="local")
    store.record(
        DISLIKE, url=disliked.url, text=disliked.embedding_text, matched_area="local"
    )

    target = article("target")
    lookup = table(
        **{
            liked.embedding_text: LOCAL,
            disliked.embedding_text: TECH,
            target.embedding_text: FLAVOUR,
        }
    )

    class RecordingFake(FakeEmbeddings):
        def __init__(self, table):
            super().__init__(table)
            self.calls: list[list[str]] = []

        def embed(self, texts):
            self.calls.append(list(texts))
            return super().embed(texts)

    client = RecordingFake(lookup)
    score_articles([target], profile=profile(), client=client, feedback=store)

    assert liked.embedding_text in client.calls[0]
    assert disliked.embedding_text in client.calls[0]
