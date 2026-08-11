"""Ingest -> dedup must produce the same articles however the pool is scheduled.

This is the regression test for the risk the thread pool introduced. Dedup sorts
by published date with a stable sort, so when two feeds carry the same story at
the same timestamp the survivor is decided purely by ingest order. Feed fetches
now finish in an unpredictable order, so if ingest ever returned results in
completion order this pair of stages would start silently picking a different
copy — same article count, different sources, no error anywhere.

Stops before scoring on purpose: the stages past dedup need the embeddings API,
and the ordering guarantee is fully exercised by these two.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone

from esp_news.nodes.dedup import dedup_articles
from esp_news.nodes.ingest import ingest_articles
from tests.conftest import FakeParsed, entry, make_config

NOW = datetime.now(timezone.utc)
SHARED_MOMENT = NOW - timedelta(hours=2)
FEEDS = [f"Feed{i:02d}" for i in range(12)]


def _install_jittered_feeds(feed_router, jitter: random.Random):
    """Every feed carries the same two duplicated stories, at the same instant.

    Latency is random per fetch, so completion order differs run to run while
    the correct answer never does.
    """
    for name in FEEDS:
        def handler(url, name=name):
            time.sleep(jitter.uniform(0.005, 0.08))
            return FakeParsed(
                [
                    entry(f"{name} own story", when=NOW - timedelta(hours=1)),
                    # Same canonical URL from every feed -> url-dupe tie.
                    dict(
                        entry("Wire exclusive: fab breaks ground", when=SHARED_MOMENT),
                        link="https://wire.test/exclusive",
                    ),
                    # Same title from every feed -> title-similarity tie.
                    entry("Council debates tourist apartment licences",
                          when=SHARED_MOMENT),
                ]
            )

        feed_router(f"{name.lower()}.", handler)


def _run_once(feed_router, seed: int):
    _install_jittered_feeds(feed_router, random.Random(seed))
    articles = ingest_articles(make_config(FEEDS))
    deduped = dedup_articles(articles)
    return [(a.source, a.title) for a in deduped]


def test_dedup_survivors_do_not_depend_on_fetch_completion_order(feed_router):
    runs = [_run_once(feed_router, seed) for seed in (1, 2, 3, 4)]

    assert runs[0] == runs[1] == runs[2] == runs[3], (
        "dedup output changed between runs — ingest is leaking completion order"
    )

    # The ties must actually have been exercised, or this test proves nothing.
    titles = [t for _, t in runs[0]]
    assert titles.count("Wire exclusive: fab breaks ground") == 1
    assert titles.count("Council debates tourist apartment licences") == 1

    # And the survivor is the first feed in config order, not whoever won the race.
    survivors = {t: s for s, t in runs[0]}
    assert survivors["Wire exclusive: fab breaks ground"] == FEEDS[0]
    assert survivors["Council debates tourist apartment licences"] == FEEDS[0]


def test_every_feeds_own_story_survives_in_feed_order(feed_router):
    """The non-duplicated articles stay one per feed, in config order."""
    result = _run_once(feed_router, seed=7)
    own = [s for s, t in result if t.endswith("own story")]
    assert own == FEEDS
