"""Phase 4 — curate node.

Turns a pile of scored articles into a front page: rank by score, drop
anything already shown on a previous run, and stop any one interest area from
taking over the digest.

That last part isn't decoration. The corpus is lopsided — the Barcelona papers
alone publish more per day than every tech blog combined — so a pure top-N by
score would hand back a page of Spanish city news on most days. The per-area
cap is a diversity preference rather than a hard limit: if caps leave the
digest short, a second pass fills the remaining slots by score alone.
"""

from __future__ import annotations

import logging
import random
from collections import Counter

from langsmith import traceable

from esp_news.models import Article
from esp_news.seen import SeenStore

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 10
DEFAULT_PER_AREA_CAP = 3

# The wildcard is drawn from the worst-scoring quarter of everything that
# cleared the filters, not from the single lowest score. The very bottom is
# reliably the same kind of thing every day — a two-line sports result, a
# weather bulletin — which is a bad match for the profile without being a new
# topic. A quarter is wide enough that the pick moves around run to run.
DEFAULT_WILDCARD_POOL = 0.25

# Articles with no summary are dropped from the front page. They score fine —
# a rolling "Live updates: Today's South Florida news" index page is a perfect
# semantic match for local news — but there is nothing to read once you open
# one, which is exactly what the display's detail view is for. Their URLs also
# tend to carry the date, so the seen store can't suppress them: they'd top
# every digest forever.
DEFAULT_MIN_SUMMARY_CHARS = 1


def _pick_wildcard(
    ranked: list[Article],
    *,
    exclude: set[str],
    pool: float = DEFAULT_WILDCARD_POOL,
    rng: random.Random | None = None,
) -> Article | None:
    """One article from the bottom of ``ranked``, flagged as the wildcard.

    ``ranked`` is best-first and already filtered, so the tail is genuinely
    off-profile rather than merely unread. Returns None when everything left
    is already on the front page, which is what a thin corpus looks like.
    """
    candidates = [a for a in ranked if a.url not in exclude]
    if not candidates:
        return None

    # At least one candidate however small the corpus, and never so wide a pool
    # that it starts overlapping the stories that actually made the page.
    depth = max(1, min(len(candidates), round(len(candidates) * pool)))
    bottom = candidates[-depth:]

    chosen = (rng or random).choice(bottom)
    return chosen.model_copy(update={"is_wildcard": True})


@traceable(run_type="chain", name="curate")
def curate_articles(
    scored: list[Article],
    *,
    top_n: int = DEFAULT_TOP_N,
    per_area_cap: int | None = DEFAULT_PER_AREA_CAP,
    seen: SeenStore | None = None,
    min_summary_chars: int = DEFAULT_MIN_SUMMARY_CHARS,
    wildcard: bool = True,
    wildcard_pool: float = DEFAULT_WILDCARD_POOL,
    rng: random.Random | None = None,
) -> list[Article]:
    """Rank, filter and cap scored articles down to the digest's front page.

    ``seen`` suppresses articles carried over from earlier digests; pass None
    to disable cross-run suppression. ``per_area_cap`` of None or 0 disables
    the diversity cap. ``min_summary_chars`` of 0 keeps summary-less articles.
    Returned articles are ordered best-scoring first — grouping for
    readability happens at render time.

    ``wildcard`` appends one deliberately low-scoring article after the ranked
    page, flagged with ``is_wildcard``. It is the only article here that isn't
    chosen by the fitness function, and that's the point: the profile can only
    return more of what it already knows about, so the digest needs one slot it
    doesn't control. Pass ``rng`` to make the pick reproducible.
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
        fresh = [a for a in ranked if not seen.contains(a.url)]
        repeats = len(ranked) - len(fresh)
        if repeats:
            logger.info("Curate: skipped %d articles seen in earlier digests", repeats)
        ranked = fresh

    cap = per_area_cap if per_area_cap and per_area_cap > 0 else None

    # 2. First pass — best first, honouring the per-area cap.
    picked: list[Article] = []
    picked_urls: set[str] = set()
    area_counts: Counter[str] = Counter()
    capped_out = 0

    for art in ranked:
        if len(picked) >= top_n:
            break
        area = art.matched_area or "unscored"
        if cap and area_counts[area] >= cap:
            capped_out += 1
            continue
        picked.append(art)
        picked_urls.add(art.url)
        area_counts[area] += 1

    # 3. Second pass — if the caps left the page short, backfill by score.
    if len(picked) < top_n and capped_out:
        for art in ranked:
            if len(picked) >= top_n:
                break
            if art.url in picked_urls:
                continue
            picked.append(art)
            picked_urls.add(art.url)
            area_counts[art.matched_area or "unscored"] += 1
        logger.info("Curate: backfilled to %d after per-area caps", len(picked))

    # Backfill appends out of order; restore the score ranking.
    picked.sort(key=lambda a: a.score or 0.0, reverse=True)

    # 4. The exploration slot — appended after the sort, so it stays last
    #    however badly (or well) it happens to have scored.
    if wildcard:
        pick = _pick_wildcard(ranked, exclude=picked_urls, pool=wildcard_pool, rng=rng)
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
