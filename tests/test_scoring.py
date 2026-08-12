"""Tests for `avoid:` — the first thing in the profile that can lower a score.

Which makes it the first thing that can quietly delete articles I wanted.
Nearly everything below is a guard on how far it is allowed to reach: one area
only, never past zero, and never at all when no avoid list is present.

Embeddings are faked throughout. Real ones would make these tests a measurement
of OpenAI rather than of this code; the arithmetic being checked here is ours.
"""

from __future__ import annotations

import numpy as np
import pytest

from esp_news.config.interests import InterestArea, InterestProfile
from esp_news.models import Article
from esp_news.nodes.score import DEFAULT_AVOID_LAMBDA, score_articles


class FakeEmbeddings:
    """Embeds text as a unit vector picked from a lookup table.

    Every text a test uses must appear in ``table``; anything else is a typo in
    the test rather than a case worth having a default for.
    """

    def __init__(self, table: dict[str, list[float]]):
        self.table = table

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = []
        for t in texts:
            if t not in self.table:
                raise KeyError(f"FakeEmbeddings has no vector for {t!r}")
            v = np.asarray(self.table[t], dtype=float)
            rows.append(v / np.linalg.norm(v))
        return np.vstack(rows)


def article(title: str, summary: str = "body") -> Article:
    return Article(
        title=title,
        url=f"https://example.test/{title}",
        summary=summary,
        source="Test Feed",
        theme="t",
    )


def text_of(art: Article) -> str:
    """The key the score node will look the article up under."""
    return f"{art.title}\n\n{art.summary}".strip()


# Three orthogonal axes are enough to build any similarity we need by hand.
WANTED = [1.0, 0.0, 0.0]
SKIPPED = [0.0, 1.0, 0.0]
ELSEWHERE = [0.0, 0.0, 1.0]


def test_avoid_penalises_only_the_article_that_matches_it():
    """The wanted article keeps its score; the skipped one loses a quarter of it."""
    on_topic, off_topic = article("on topic"), article("off topic")
    profile = InterestProfile(
        areas=[
            InterestArea(
                name="local",
                description="local news",
                avoid=["not my city"],
            )
        ]
    )
    client = FakeEmbeddings(
        {
            "local news": WANTED,
            "not my city": SKIPPED,
            text_of(on_topic): WANTED,
            # Half-and-half: matches the area, but matches the avoid just as well.
            text_of(off_topic): [1.0, 1.0, 0.0],
        }
    )

    scored = score_articles(
        [on_topic, off_topic], profile=profile, client=client, avoid_lambda=0.5
    )

    # Untouched: cosine 1.0 with the reference, 0.0 with the avoid.
    assert scored[0].score == pytest.approx(1.0)
    # 0.7071 against both, so 0.7071 - 0.5 * 0.7071.
    assert scored[1].score == pytest.approx(0.7071 * 0.5, abs=1e-3)
    assert scored[0].score > scored[1].score


def test_avoid_is_scoped_to_its_own_area():
    """An avoid in one area must not touch what another area says about the same article."""
    art = article("shared")
    profile = InterestProfile(
        areas=[
            InterestArea(name="penalised", description="area one", avoid=["nope"]),
            InterestArea(name="untouched", description="area two"),
        ]
    )
    client = FakeEmbeddings(
        {
            "area one": WANTED,
            "area two": WANTED,
            "nope": WANTED,  # fires at full strength — worst case for leakage
            text_of(art): WANTED,
        }
    )

    scored = score_articles([art], profile=profile, client=client, avoid_lambda=0.5)

    assert scored[0].area_scores["penalised"] == pytest.approx(0.5)
    assert scored[0].area_scores["untouched"] == pytest.approx(1.0)
    # The article survives on the area that never opted into a negative.
    assert scored[0].matched_area == "untouched"
    assert scored[0].score == pytest.approx(1.0)


def test_avoid_clips_at_zero_and_never_goes_negative():
    """A savage penalty costs the area the article, not the article its score."""
    art = article("clipped")
    profile = InterestProfile(
        areas=[
            InterestArea(name="hostile", description="area", avoid=["avoid me"]),
            InterestArea(name="neutral", description="other area"),
        ]
    )
    client = FakeEmbeddings(
        {
            "area": WANTED,
            "avoid me": WANTED,
            "other area": ELSEWHERE,
            text_of(art): WANTED,
        }
    )

    scored = score_articles([art], profile=profile, client=client, avoid_lambda=5.0)

    # 1.0 - 5.0 * 1.0 would be -4.0 without the clip.
    assert scored[0].area_scores["hostile"] == 0.0
    assert scored[0].score >= 0.0


def test_areas_without_an_avoid_list_score_exactly_as_before():
    """The feature has to be inert for the eight areas that don't use it."""
    art = article("plain")
    profile = InterestProfile(areas=[InterestArea(name="plain", description="topic")])
    client = FakeEmbeddings({"topic": WANTED, text_of(art): [1.0, 1.0, 0.0]})

    with_feature = score_articles([art], profile=profile, client=client)
    without = score_articles([art], profile=profile, client=client, avoid_lambda=0.0)

    assert with_feature[0].score == without[0].score == pytest.approx(0.7071, abs=1e-3)


def test_avoid_lambda_of_zero_disables_a_configured_avoid_list():
    """The escape hatch: score positives only, without editing interests.yaml."""
    art = article("skipped")
    profile = InterestProfile(
        areas=[InterestArea(name="area", description="topic", avoid=["avoid me"])]
    )
    client = FakeEmbeddings(
        {"topic": WANTED, "avoid me": WANTED, text_of(art): WANTED}
    )

    assert score_articles([art], profile=profile, client=client, avoid_lambda=0.0)[
        0
    ].score == pytest.approx(1.0)
    assert score_articles([art], profile=profile, client=client)[0].score < 1.0


def test_avoid_applies_before_the_area_weight():
    """Lambda has to mean the same thing in a 1.2x area as in a 0.9x one.

    If the penalty were applied after the weight, an avoid entry would bite
    harder in areas that happen to be weighted down, which is unrelated to
    anything the profile is trying to say.
    """
    art = article("weighted")
    texts = {"topic": WANTED, "avoid me": [1.0, 1.0, 0.0], text_of(art): WANTED}
    heavy = InterestProfile(
        areas=[InterestArea(name="a", description="topic", avoid=["avoid me"], weight=2.0)]
    )
    light = InterestProfile(
        areas=[InterestArea(name="a", description="topic", avoid=["avoid me"], weight=1.0)]
    )

    hi = score_articles([art], profile=heavy, client=FakeEmbeddings(texts), avoid_lambda=0.5)
    lo = score_articles([art], profile=light, client=FakeEmbeddings(texts), avoid_lambda=0.5)

    # Tolerance is the score field's own rounding: both sides are stored to 4dp,
    # so doubling one of them can't land exactly on the other.
    assert hi[0].score == pytest.approx(2.0 * lo[0].score, abs=1e-3)


def test_default_lambda_is_a_nudge_not_a_veto():
    """Guards the sizing decision: a full-strength avoid must not halve a score.

    0.15 was chosen as the smallest value that moved the article the form asked
    to move. If someone raises it later, this is the test that should make them
    justify it.
    """
    assert 0 < DEFAULT_AVOID_LAMBDA <= 0.25
