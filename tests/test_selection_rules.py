"""Tests for the 2026-10-06 selection rules: per-area limits and floors, one
article per story, the coverage boost, and the per-area summary focus.

Each one exists because of a specific bad front page — four Time Out lists in
one digest, a lone Australian story beating a five-outlet election — so the
tests pin the rule rather than any particular score.
"""

from __future__ import annotations

from esp_news.clients.summarizer import Summarizer
from esp_news.config.interests import InterestArea, InterestProfile
from esp_news.models import Article
from esp_news.nodes.curate import curate_articles
from esp_news.nodes.score import COVERAGE_MAX_OUTLETS, score_articles
from tests.test_scoring import FakeEmbeddings, text_of


def scored(title: str, area: str, score: float, story: str | None = None) -> Article:
    return Article(
        title=title,
        url=f"https://example.test/{title}",
        summary="body",
        source="Test Feed",
        theme="t",
        score=score,
        base_score=score,
        matched_area=area,
        story_key=story or f"https://example.test/{title}",
    )


def page(articles, **kwargs) -> list[Article]:
    kwargs.setdefault("wildcard", False)
    return curate_articles(articles, seen=None, **kwargs)


# ── curate ──────────────────────────────────────────────────────────────────


def test_area_limit_holds_even_when_the_page_is_short():
    """The backfill breaks the soft cap; it must not break max_per_digest."""
    plans = [scored(f"plan-{i}", "barcelona_dates", 0.9 - i / 100) for i in range(6)]
    out = page(plans, top_n=5, area_limits={"barcelona_dates": 1})
    assert [a.title for a in out] == ["plan-0"]


def test_area_limit_replaces_the_soft_cap_upwards():
    world = [scored(f"w-{i}", "world_politics", 0.9 - i / 100) for i in range(6)]
    out = page(world, top_n=10, per_area_cap=3, area_limits={"world_politics": 5})
    assert len(out) == 5


def test_floor_reserves_slots_against_higher_scoring_areas():
    loud = [scored(f"startup-{i}", f"area-{i}", 0.9) for i in range(10)]
    world = [scored(f"w-{i}", "world_politics", 0.5 - i / 100) for i in range(5)]
    out = page(loud + world, top_n=10, area_floors={"world_politics": 3})
    assert sum(a.matched_area == "world_politics" for a in out) == 3
    assert len(out) == 10


def test_unfillable_floor_backfills_instead_of_leaving_holes():
    loud = [scored(f"a-{i}", f"area-{i}", 0.9) for i in range(10)]
    world = [scored("w-0", "world_politics", 0.5)]
    out = page(loud + world, top_n=10, area_floors={"world_politics": 3})
    assert len(out) == 10
    assert "w-0" in {a.title for a in out}


def test_one_article_per_story():
    brazil = [scored(f"brazil-{i}", f"area-{i}", 0.9 - i / 100, story="brazil") for i in range(4)]
    other = [scored("other", "area-x", 0.5)]
    out = page(brazil + other, top_n=10)
    assert [a.title for a in out] == ["brazil-0", "other"]


def test_wildcard_respects_limits_and_stories():
    on_page = scored("plan-0", "barcelona_dates", 0.9, story="s")
    rest = [scored(f"plan-{i}", "barcelona_dates", 0.5) for i in range(1, 20)]
    out = curate_articles(
        [on_page, *rest], top_n=1, seen=None, area_limits={"barcelona_dates": 1}
    )
    assert [a.title for a in out] == ["plan-0"]


# ── score ───────────────────────────────────────────────────────────────────


def _profile(boost: float) -> InterestProfile:
    return InterestProfile(
        areas=[InterestArea(name="world", references=["news"], coverage_boost=boost)]
    )


def _story(title: str, source: str) -> Article:
    return Article(title=title, url=f"https://x.test/{source}/{title}", source=source, theme="t")


def test_coverage_counts_other_outlets_and_boosts_the_score():
    big = [_story("brazil", s) for s in ("BBC", "Guardian", "FT")]
    lone = _story("australia", "Guardian")
    same_feed = _story("brazil-again", "BBC")  # a feed can't vouch for itself
    arts = [*big, lone, same_feed]
    table = {"news": [1, 0, 0]}
    for a in big + [same_feed]:
        table[text_of(a)] = [1, 1, 0]
    table[text_of(lone)] = [1, 0, 1]

    out = {a.url: a for a in score_articles(arts, profile=_profile(0.03), client=FakeEmbeddings(table))}
    plain = {a.url: a for a in score_articles(arts, profile=_profile(0.0), client=FakeEmbeddings(table))}

    bbc = big[0].url
    assert out[bbc].coverage == 2  # Guardian, FT — not the other BBC piece
    assert out[lone.url].coverage == 0
    assert out[bbc].score == round(plain[bbc].score + 0.03 * 2, 4)
    assert out[lone.url].score == plain[lone.url].score
    assert len({out[a.url].story_key for a in big}) == 1
    assert out[lone.url].story_key != out[bbc].story_key


def test_coverage_boost_is_bounded():
    arts = [_story("story", f"S{i}") for i in range(COVERAGE_MAX_OUTLETS + 4)]
    table = {"news": [1, 0, 0], **{text_of(a): [1, 1, 0] for a in arts}}
    out = score_articles(arts, profile=_profile(0.03), client=FakeEmbeddings(table))
    plain = score_articles(arts, profile=_profile(0.0), client=FakeEmbeddings(table))
    assert abs(out[0].score - plain[0].score - 0.03 * COVERAGE_MAX_OUTLETS) < 1e-3


# ── summarize ───────────────────────────────────────────────────────────────


def test_focus_reaches_the_prompt_and_only_changes_the_key_when_set():
    s = Summarizer(cache_path=None)
    assert s._key("t", "body") == s._key("t", "body", "")
    assert s._key("t", "body") != s._key("t", "body", "explain the startup")

    seen: list[str] = []

    class Client:
        class responses:
            @staticmethod
            def create(**kw):
                seen.append(kw["input"])
                return type("R", (), {"output_text": "ok"})()

    s._openai = lambda: Client
    s.summarize("t", "src", "body text", focus="explain the startup")
    s.summarize("u", "src", "body text")
    assert "Focus for this summary: explain the startup" in seen[0]
    assert "Focus" not in seen[1]
