"""Tests for the wildcard slot — the one article not chosen by the fitness function.

Tested for where it draws from rather than for what it draws. The draw is random
on purpose, so these pin the slice and use a seeded Random only to make "which
member of the slice" reproducible. The uniformity test exists so a later change
cannot quietly turn this into a ranked pick.
"""

from __future__ import annotations

import random

import pytest

from esp_news.models import Article
from esp_news.nodes.curate import DEFAULT_WILDCARD_BAND, _pick_wildcard


def article(title: str, summary: str = "body") -> Article:
    return Article(
        title=title,
        url=f"https://example.test/{title}",
        summary=summary,
        source="Test Feed",
        theme="t",
    )


def ranked_articles(n: int) -> list[Article]:
    """``n`` articles, best-first, scored 1.00 down to 0.01."""
    out = []
    for i in range(n):
        art = article(f"rank-{i:03d}")
        out.append(art.model_copy(update={"score": (n - i) / n, "matched_area": "a"}))
    return out


def test_wildcard_draws_from_the_middle_not_the_bottom():
    """The whole point of the change: the tail is no longer eligible."""
    ranked = ranked_articles(100)
    seen = {
        _pick_wildcard(ranked, exclude=set(), rng=random.Random(s)).title
        for s in range(200)
    }
    positions = sorted(int(t.split("-")[1]) for t in seen)

    # 40th-70th percentile of 100 articles, best-first, is indices 30..59.
    assert positions[0] == 30
    assert positions[-1] == 59
    assert ranked[0].title not in seen, "the top of the page must never be the wildcard"
    assert ranked[-1].title not in seen, "the old bottom-quartile behaviour is gone"


def test_wildcard_band_is_configurable():
    ranked = ranked_articles(100)
    seen = {
        _pick_wildcard(ranked, exclude=set(), band=(0.0, 0.25), rng=random.Random(s)).title
        for s in range(200)
    }
    positions = sorted(int(t.split("-")[1]) for t in seen)
    # The bottom quarter — indices 75..99 — i.e. the previous default.
    assert positions[0] == 75
    assert positions[-1] == 99


def test_wildcard_draw_is_uniform_over_the_band():
    """It stays a genuine coin toss — no ranking or seeding inside the band."""
    ranked = ranked_articles(10)
    counts: dict[str, int] = {}
    for s in range(3000):
        pick = _pick_wildcard(ranked, exclude=set(), rng=random.Random(s))
        counts[pick.title] = counts.get(pick.title, 0) + 1

    # Band covers indices 3..5, three articles, so each should land near a third.
    assert len(counts) == 3
    for n in counts.values():
        assert 0.28 < n / 3000 < 0.39


@pytest.mark.parametrize("n", [1, 2, 3, 5])
def test_wildcard_survives_a_tiny_corpus(n):
    """Rounding must not produce an empty slice on a thin day."""
    pick = _pick_wildcard(ranked_articles(n), exclude=set(), rng=random.Random(0))
    assert pick is not None and pick.is_wildcard


def test_wildcard_returns_none_when_everything_is_already_on_the_page():
    ranked = ranked_articles(4)
    assert _pick_wildcard(ranked, exclude={a.url for a in ranked}) is None


def test_wildcard_band_default_is_mid_pack():
    low, high = DEFAULT_WILDCARD_BAND
    assert 0.0 < low < high < 1.0


def test_feedback_cannot_steer_the_wildcard():
    """The exploration slot is drawn from the written profile's ranking.

    The wildcard exists because the profile can only ever hand back more of what
    it already knows about. Letting like/dislike verdicts reorder the pool it is
    drawn from would point the one unsteered slot at the same place everything
    else already points.

    So the band is measured on ``base_score``. Here the same articles are given
    a wildly different ``score`` — reversed, so no ranking survives — and the
    same seed has to draw the same article.
    """
    ranked = ranked_articles(100)
    with_base = [
        a.model_copy(update={"base_score": a.score}) for a in ranked
    ]
    # Same articles, same base_score, feedback-mangled `score` running backwards.
    n = len(with_base)
    shuffled = sorted(
        (
            a.model_copy(update={"score": (i + 1) / n})
            for i, a in enumerate(with_base)
        ),
        key=lambda a: a.score,
        reverse=True,
    )

    for seed in range(50):
        plain = _pick_wildcard(with_base, exclude=set(), rng=random.Random(seed))
        moved = _pick_wildcard(shuffled, exclude=set(), rng=random.Random(seed))
        assert plain.title == moved.title


def test_the_band_falls_back_to_score_when_there_is_no_base_score():
    """Articles built before base_score existed still land in the same band."""
    ranked = ranked_articles(100)
    assert all(a.base_score is None for a in ranked)

    picks = {
        _pick_wildcard(ranked, exclude=set(), rng=random.Random(s)).title
        for s in range(200)
    }
    positions = sorted(int(t.split("-")[1]) for t in picks)
    assert positions[0] == 30 and positions[-1] == 59
