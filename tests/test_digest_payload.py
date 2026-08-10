"""The wildcard has to survive the trip from curate to the device.

``tests/test_curate.py`` proves the wildcard is *picked*. This file covers the
step after that, which is where it went missing in practice: the payload written
to ``digests/latest.json`` and served to the firmware and the widget. Both
readers style the card off a single ``wildcard`` boolean, so if that field is
absent or the article is trimmed away, the exploration slot simply isn't there —
and a digest of ten ordinary stories looks completely normal.

Trimming is the sharp edge. The wildcard lives at the tail of the list, and the
obvious ``articles[:limit]`` cuts from the tail, so on exactly the days the page
is full the one deliberately-added article is the one dropped. That is silent,
and it is intermittent, which is the worst pair.
"""

from __future__ import annotations

from esp_news.models import Article
from esp_news.nodes.digest import DEFAULT_JSON_LIMIT, digest_payload


def article(title: str, *, score: float, wildcard: bool = False) -> Article:
    return Article(
        title=title,
        url=f"https://example.test/{title}",
        summary="body",
        source="Test Feed",
        theme="t",
        score=score,
        matched_area="spain",
        is_wildcard=wildcard,
    )


def page(n: int, *, wildcard: bool = True) -> list[Article]:
    """``n`` ranked articles, best-first, with the wildcard appended last.

    The wildcard scores below all of them, exactly as it does in a real digest:
    it is drawn from the middle of the ranking, which is by definition under
    everything that made the front page on merit.
    """
    ranked = [article(f"rank-{i:02d}", score=0.9 - i * 0.01) for i in range(n)]
    if not wildcard:
        return ranked
    return ranked + [article("wild", score=0.40, wildcard=True)]


def test_every_article_carries_the_flag_the_ui_styles_from():
    """Both readers key off this field; absent, they fall back to the area."""
    articles = digest_payload(page(3))["articles"]
    assert [a["wildcard"] for a in articles] == [False, False, False, True]


def test_the_wildcard_is_last():
    """It is the exploration slot, not a result — it sits after the ranking."""
    articles = digest_payload(page(5))["articles"]
    assert articles[-1]["title"] == "wild"


def test_a_full_page_drops_a_ranked_story_not_the_wildcard():
    """The regression this file exists for.

    A plain slice would cut the tail, which is the wildcard, and only on the days
    the page happens to be full — so the feature would look like it worked most
    of the time.
    """
    articles = digest_payload(page(DEFAULT_JSON_LIMIT + 5))["articles"]

    assert len(articles) == DEFAULT_JSON_LIMIT
    assert articles[-1]["title"] == "wild"
    assert sum(a["wildcard"] for a in articles) == 1


def test_the_device_gets_a_full_page_plus_the_wildcard():
    """11 stories from a default run: top_n 10, and the slot appended after.

    NEWS_MAX_ARTICLES is 12, so the whole page reaches the panel with a slot to
    spare — worth pinning, because the two numbers are set in different repos'
    worth of code and only meet here.
    """
    payload = digest_payload(page(10))
    assert payload["count"] == 11
    assert payload["articles"][10]["wildcard"] is True


def test_a_digest_with_no_wildcard_is_still_well_formed():
    """``--no-wildcard`` is a supported run, not a broken one."""
    payload = digest_payload(page(4, wildcard=False))
    assert payload["count"] == 4
    assert not any(a["wildcard"] for a in payload["articles"])
