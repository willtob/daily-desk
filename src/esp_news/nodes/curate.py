"""Phase 4 — curate node.

Turns a pile of scored articles into a front page: rank by score, drop
anything already shown on a previous run, and stop any one interest area from
taking over the digest.

That last part isn't decoration. The corpus is lopsided — the Barcelona papers
alone publish more per day than every tech blog combined — so a pure top-N by
score would hand back a page of Spanish city news on most days. The per-area
cap is a diversity preference rather than a hard limit: if caps leave the
digest short, a second pass fills the remaining slots by score alone.

Areas can also set their own bounds in interests.yaml. ``max_per_digest`` is a
hard limit that the backfill never breaks — one "things to do in Barcelona"
list a day is plenty, however well the rest score — and ``min_per_digest``
reserves slots for an area, which is how world news holds its share of the page
on a day the startup feeds are loud. And whatever the area, one story gets one
slot: five outlets on the same election is a reason to rank it, not to show it
five times.
"""

from __future__ import annotations

import logging
import random
from collections import Counter

from langsmith import traceable

from esp_news.models import Article
from esp_news.storage.seen import SeenStore

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 10
DEFAULT_PER_AREA_CAP = 3

# The wildcard is drawn from a band in the middle of the ranking, given as
# percentiles of the score distribution: the 40th to the 70th.
#
# It used to be the bottom quarter, on the theory that the furthest thing from
# the profile was the most surprising. Ten digests said otherwise — the bottom
# is reliably the same handful of shapes every day, a two-line sports result or
# a weather bulletin, which is randomness without discovery. The pleasant
# surprise lives mid-pack: adjacent to something I care about, but not central
# enough to win a slot on its own. High enough to be about one of my topics,
# low enough that the ranker was never going to show it to me.
#
# The band is wide enough that the pick genuinely moves run to run. It stays a
# uniform random draw over that band — nothing here ranks or seeds it.
DEFAULT_WILDCARD_BAND = (0.40, 0.70)

# Articles with no summary are dropped from the front page. They score fine —
# a rolling "Live updates: Today's South Florida news" index page is a perfect
# semantic match for local news — but there is nothing to read once you open
# one, which is exactly what the display's detail view is for. Their URLs also
# tend to carry the date, so the seen store can't suppress them: they'd top
# every digest forever.
DEFAULT_MIN_SUMMARY_CHARS = 1


def _written_score(article: Article) -> float:
    """The score interests.yaml alone gave an article.

    Falls back to ``score`` for articles that predate ``base_score`` or were
    built by hand, which is the same number whenever there is no feedback.
    """
    if article.base_score is not None:
        return article.base_score
    return article.score or 0.0


def _pick_wildcard(
    ranked: list[Article],
    *,
    exclude: set[str],
    band: tuple[float, float] = DEFAULT_WILDCARD_BAND,
    rng: random.Random | None = None,
) -> Article | None:
    """One article from the middle of ``ranked``, flagged as the wildcard.

    ``ranked`` is best-first and already filtered. ``band`` is a pair of score
    percentiles, low first, so ``(0.40, 0.70)`` means "somewhere between the
    40th and the 70th percentile". Returns None when everything left is already
    on the front page, which is what a thin corpus looks like.

    The band is measured on ``base_score`` — the written profile alone — so
    like/dislike verdicts cannot steer the exploration slot toward more of what
    they already know about. The one residue worth naming: feedback still
    changes *which* articles the front page took, so the pool this draws from
    differs by whatever the page swallowed. The ordering inside the pool is
    feedback-free; its membership is not, and it cannot be without letting the
    wildcard duplicate a story already on the page.
    """
    candidates = [a for a in ranked if a.url not in exclude]
    if not candidates:
        return None
    candidates = sorted(candidates, key=_written_score, reverse=True)

    # Percentiles count up from the worst article; ``candidates`` counts down
    # from the best. The 70th percentile is therefore 30% of the way in from the
    # front, and the 40th is 60% of the way in — so the high percentile gives
    # the slice's start and the low one gives its end.
    low, high = band
    n = len(candidates)
    start = min(n - 1, round(n * (1.0 - high)))
    stop = max(start + 1, round(n * (1.0 - low)))
    middle = candidates[start:stop]

    chosen = (rng or random).choice(middle)
    return chosen.model_copy(update={"is_wildcard": True})


@traceable(run_type="chain", name="curate")
def curate_articles(
    scored: list[Article],
    *,
    top_n: int = DEFAULT_TOP_N,
    per_area_cap: int | None = DEFAULT_PER_AREA_CAP,
    area_limits: dict[str, int] | None = None,
    area_floors: dict[str, int] | None = None,
    seen: SeenStore | None = None,
    min_summary_chars: int = DEFAULT_MIN_SUMMARY_CHARS,
    wildcard: bool = True,
    wildcard_band: tuple[float, float] = DEFAULT_WILDCARD_BAND,
    rng: random.Random | None = None,
) -> list[Article]:
    """Rank, filter and cap scored articles down to the digest's front page.

    ``seen`` suppresses articles carried over from earlier digests; pass None
    to disable cross-run suppression. ``per_area_cap`` of None or 0 disables
    the diversity cap. ``min_summary_chars`` of 0 keeps summary-less articles.
    Returned articles are ordered best-scoring first — grouping for
    readability happens at render time.

    ``area_limits`` maps an area to the most slots it may ever take — unlike
    ``per_area_cap`` it holds through the backfill and the wildcard, and it
    replaces the soft cap for that area. ``area_floors`` maps an area to the
    slots reserved for it, filled when the corpus has the articles. Both come
    from ``max_per_digest`` / ``min_per_digest`` in interests.yaml. Whatever the
    limits, only one article per ``story_key`` makes the page.

    ``wildcard`` appends one mid-ranked article after the ranked page, flagged
    with ``is_wildcard``. It is the only article here that isn't chosen by the
    fitness function, and that's the point: the profile can only return more of
    what it already knows about, so the digest needs one slot it doesn't
    control. ``wildcard_band`` is the pair of score percentiles it is drawn
    from. Pass ``rng`` to make the pick reproducible.
    """
    if not scored:
        logger.info("No articles to curate")
        return []

    ranked = sorted(scored, key=lambda a: a.score or 0.0, reverse=True)

    # 0. Drop articles with nothing to read. Done before ranking rather than in
    #    an earlier node so the corpus stays intact for scoring and debugging.
    if min_summary_chars > 0:
        readable = [a for a in ranked if len(a.summary.strip()) >= min_summary_chars]
        dropped = len(ranked) - len(readable)
        if dropped:
            by_source = Counter(
                a.source
                for a in ranked
                if len(a.summary.strip()) < min_summary_chars
            )
            logger.info(
                "Curate: dropped %d articles with no usable summary (%s)",
                dropped,
                ", ".join(f"{s}={n}" for s, n in by_source.most_common(4)),
            )
        ranked = readable

    # 1. Drop anything a previous digest already carried.
    if seen is not None:
        fresh = [a for a in ranked if not seen.contains(a.url, a.published)]
        repeats = len(ranked) - len(fresh)
        if repeats:
            logger.info("Curate: skipped %d articles seen in earlier digests", repeats)
        ranked = fresh

    cap = per_area_cap if per_area_cap and per_area_cap > 0 else None
    limits = area_limits or {}
    floors = {a: n for a, n in (area_floors or {}).items() if n > 0}

    picked: list[Article] = []
    picked_urls: set[str] = set()
    picked_stories: set[str] = set()
    area_counts: Counter[str] = Counter()
    capped_out = 0

    def _area(art: Article) -> str:
        return art.matched_area or "unscored"

    def _blocked(art: Article) -> bool:
        """Never on the page, whichever pass is asking: a repeat of a story
        already there, or an area at its hard ``max_per_digest``."""
        if art.url in picked_urls:
            return True
        if art.story_key and art.story_key in picked_stories:
            return True
        area = _area(art)
        return area in limits and area_counts[area] >= limits[area]

    def _take(art: Article) -> None:
        picked.append(art)
        picked_urls.add(art.url)
        if art.story_key:
            picked_stories.add(art.story_key)
        area_counts[_area(art)] += 1

    def _floor_shortfall() -> int:
        return sum(max(0, n - area_counts[a]) for a, n in floors.items())

    # 2. First pass — best first, honouring the soft per-area cap and the
    #    explicit limits, and holding back enough slots for any area floor that
    #    isn't met yet.
    for art in ranked:
        if len(picked) >= top_n:
            break
        if _blocked(art):
            continue
        area = _area(art)
        # An area with its own limit is governed by it, above or below the cap.
        if area not in limits and cap and area_counts[area] >= cap:
            capped_out += 1
            continue
        fills_floor = area_counts[area] < floors.get(area, 0)
        if not fills_floor and top_n - len(picked) <= _floor_shortfall():
            capped_out += 1
            continue
        _take(art)

    # 3. Second pass — if the caps or floors left the page short (a floor the
    #    corpus couldn't fill, say), backfill by score. The soft cap gives way
    #    here; the hard limits and the one-per-story rule do not.
    if len(picked) < top_n and capped_out:
        for art in ranked:
            if len(picked) >= top_n:
                break
            if not _blocked(art):
                _take(art)
        logger.info("Curate: backfilled to %d after per-area caps", len(picked))

    # Backfill appends out of order; restore the score ranking.
    picked.sort(key=lambda a: a.score or 0.0, reverse=True)

    # 4. The exploration slot — appended after the sort, so it stays last
    #    however badly (or well) it happens to have scored.
    if wildcard:
        pool = [a for a in ranked if not _blocked(a)]
        pick = _pick_wildcard(pool, exclude=picked_urls, band=wildcard_band, rng=rng)
        if pick is not None:
            picked.append(pick)
            area_counts[pick.matched_area or "unscored"] += 1
            logger.info(
                "Curate: wildcard %.4f [%s] %s — %s",
                pick.score or 0.0,
                pick.matched_area or "unscored",
                pick.source,
                pick.title[:60],
            )

    logger.info(
        "Curated %d of %d articles (top_n=%d%s, per_area_cap=%s): %s",
        len(picked),
        len(scored),
        top_n,
        " +1 wildcard" if any(a.is_wildcard for a in picked) else "",
        cap or "off",
        ", ".join(f"{a}={n}" for a, n in area_counts.most_common()) or "none",
    )
    return picked
