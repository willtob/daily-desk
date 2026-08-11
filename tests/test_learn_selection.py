"""Topic selection: difficulty weighting and the repeat cooldown.

``choose_topic`` is random, so these drive it with a seeded ``Random`` and
assert on distributions over many draws rather than on single outcomes. A test
that drew once and asserted the difficulty would pass or fail by luck, which is
worse than no test.

The behaviour worth pinning down is the promotion rule. Two failure directions,
both of which make the tab unpleasant rather than broken:

  * Promoting too eagerly — on two good sessions, say — turns a 78-topic bank
    into advanced-only after one lucky evening, and the way back down is five
    more sessions of being hammered.
  * Never promoting means scoring well changes nothing, and the "unless I've
    been scoring well lately" half of the feature does not exist.

The cooldown's fallback matters too: it must degrade to "an early repeat"
rather than to an exception, because the failure would land on a live request.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import date, timedelta

from esp_news.learn.topics import LearnSettings, Topic, TopicBank, choose_topic

TODAY = date(2026, 8, 10)


def make_bank(*, per_tier: int = 6, **settings) -> TopicBank:
    topics = [
        Topic(
            id=f"{difficulty}_{i}",
            name=f"{difficulty} {i}",
            difficulty=difficulty,
            covers="a sufficiently long checklist for validation purposes",
        )
        for difficulty in ("intro", "intermediate", "advanced")
        for i in range(per_tier)
    ]
    return TopicBank(settings=LearnSettings(**settings), topics=topics)


def draw_many(bank: TopicBank, *, n: int = 3000, seed: int = 0, **kwargs) -> Counter:
    rng = random.Random(seed)
    return Counter(
        choose_topic(bank, today=TODAY, rng=rng, **kwargs).difficulty for _ in range(n)
    )


# ── difficulty weighting ─────────────────────────────────────────────────────


def test_intro_dominates_with_no_history():
    """Cold start is the gentle mix: 3:2:1 across equally-sized tiers."""
    counts = draw_many(make_bank())
    assert counts["intro"] > counts["intermediate"] > counts["advanced"]
    # 3:2:1 over 3000 draws. Loose bounds — this is asserting the ordering and
    # rough magnitude, not reproducing the RNG.
    assert 0.45 < counts["intro"] / 3000 < 0.55
    assert 0.12 < counts["advanced"] / 3000 < 0.22


def test_a_full_window_of_good_scores_flips_the_mix():
    """The 'unless I've been scoring well lately' half of the feature."""
    counts = draw_many(make_bank(), recent_scores=[9, 8, 9, 8, 8])
    assert counts["advanced"] > counts["intermediate"] > counts["intro"]


def test_a_full_window_of_mediocre_scores_does_not():
    counts = draw_many(make_bank(), recent_scores=[6, 7, 5, 6, 7])
    assert counts["intro"] > counts["advanced"]


def test_two_good_sessions_are_not_enough_to_promote():
    """The eager-promotion failure, asserted directly.

    Perfect scores, but only two of them. The window is not full, so the mix
    must still be the gentle one.
    """
    settings = LearnSettings()
    assert settings.active_weights([10, 10]) == settings.weights
    counts = draw_many(make_bank(), recent_scores=[10, 10])
    assert counts["intro"] > counts["advanced"]


def test_promotion_reads_the_newest_scores_not_the_oldest():
    """recent_scores is newest-first, and only the window's worth is read.

    Reversing this is a silent bug: the mix would respond to how you were doing
    a month ago. Recent bad scores with excellent history must not promote.
    """
    settings = LearnSettings()
    assert settings.active_weights([3, 4, 3, 4, 3, 10, 10, 10]) == settings.weights
    assert settings.active_weights([10, 10, 10, 10, 10, 3, 3]) == settings.strong_weights


def test_the_promotion_boundary_is_inclusive():
    """Exactly strong_average promotes; a hair under does not."""
    settings = LearnSettings(strong_window=4, strong_average=7.5)
    assert settings.active_weights([7, 8, 7, 8]) == settings.strong_weights  # 7.5
    assert settings.active_weights([7, 8, 7, 7]) == settings.weights  # 7.25


def test_zeroed_weights_fall_back_to_uniform_rather_than_raising():
    """A hand-zeroed weight block must not take down a live request."""
    bank = make_bank(weights={"intro": 0, "intermediate": 0, "advanced": 0})
    counts = draw_many(bank, n=300)
    assert sum(counts.values()) == 300
    assert len(counts) == 3


def test_a_zeroed_tier_is_never_drawn():
    bank = make_bank(weights={"intro": 1, "intermediate": 1, "advanced": 0})
    counts = draw_many(bank, n=500)
    assert counts["advanced"] == 0
    assert counts["intro"] > 0


# ── the repeat cooldown ──────────────────────────────────────────────────────


def test_a_topic_seen_inside_the_cooldown_is_skipped():
    bank = make_bank(per_tier=2, repeat_cooldown_days=14)
    recent = {t.id: TODAY - timedelta(days=3) for t in bank.topics[:-1]}
    survivor = bank.topics[-1]

    rng = random.Random(0)
    drawn = {
        choose_topic(bank, last_seen=recent, today=TODAY, rng=rng).id for _ in range(50)
    }
    assert drawn == {survivor.id}


def test_a_topic_seen_before_the_cooldown_is_eligible_again():
    bank = make_bank(per_tier=1, repeat_cooldown_days=14)
    stale = {t.id: TODAY - timedelta(days=14) for t in bank.topics}

    rng = random.Random(0)
    drawn = {
        choose_topic(bank, last_seen=stale, today=TODAY, rng=rng).id for _ in range(50)
    }
    assert len(drawn) > 1, "a topic at exactly the cooldown boundary is eligible"


def test_the_cooldown_boundary_is_exact():
    """Day 14 is eligible, day 13 is not, for a 14-day cooldown."""
    bank = make_bank(per_tier=1, repeat_cooldown_days=14)
    target = bank.topics[0]
    others = {t.id: TODAY for t in bank.topics[1:]}

    rng = random.Random(0)
    at_boundary = {target.id: TODAY - timedelta(days=14), **others}
    assert choose_topic(bank, last_seen=at_boundary, today=TODAY, rng=rng).id == target.id

    inside = {target.id: TODAY - timedelta(days=13), **others}
    # Everything is now on cooldown, so the filter is dropped and any topic can
    # come back — but the point is that `target` was no longer privileged.
    drawn = {
        choose_topic(bank, last_seen=inside, today=TODAY, rng=rng).id for _ in range(50)
    }
    assert len(drawn) > 1


def test_an_exhausted_cooldown_still_returns_a_topic():
    """Degrade to an early repeat, never to an exception on a live request."""
    bank = make_bank(per_tier=2, repeat_cooldown_days=365)
    everything = {t.id: TODAY for t in bank.topics}

    rng = random.Random(0)
    topic = choose_topic(bank, last_seen=everything, today=TODAY, rng=rng)
    assert topic in bank.topics


def test_an_unseen_topic_is_always_eligible():
    bank = make_bank(per_tier=1, repeat_cooldown_days=9999)
    rng = random.Random(0)
    assert choose_topic(bank, last_seen={}, today=TODAY, rng=rng) in bank.topics
