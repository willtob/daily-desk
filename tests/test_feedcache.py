"""The feed cache, and the promise that only ``--cached-feeds`` reads it.

The dangerous failure here is not a cache miss, it's a cache hit nobody asked
for: a morning digest that looks completely normal and is describing yesterday.
So the tests are mostly about what does *not* happen — no read without the flag,
no network with it, no storing of a feed that errored.

The cross-check on ``--hours`` matters for the same reason. Entries are stored
before the lookback filter, so one stored copy has to serve any window; if the
filter ever moved back in front of the cache, a cached ``--hours 72`` run would
return whatever some earlier ``--hours 6`` run happened to keep, and look right
doing it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from esp_news.feedcache import STALE_AFTER_HOURS, CachedFeed, FeedCache
from esp_news.nodes.dedup import dedup_articles
from esp_news.nodes.ingest import ingest_articles
from tests.conftest import FakeParsed, entry, make_config

FEEDS = ["Alpha", "Beta", "Gamma"]


def _install(feed_router, per_feed=2):
    for name in FEEDS:
        feed_router(
            f"{name.lower()}.",
            lambda url, name=name: FakeParsed(
                [entry(f"{name} story {i}") for i in range(per_feed)]
            ),
        )


def _age_cache(cache_dir, hours: float):
    """Backdate every stored copy, so staleness can be tested without waiting."""
    when = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    for path in cache_dir.glob("*.json"):
        payload = json.loads(path.read_text())
        payload["fetched_at"] = when
        path.write_text(json.dumps(payload))


def test_without_the_flag_the_network_is_used(feed_router, isolated_feed_cache):
    _install(feed_router)

    articles = ingest_articles(make_config(FEEDS))

    assert len(feed_router.calls) == len(FEEDS)
    assert len(articles) == 2 * len(FEEDS)


def test_a_populated_cache_is_ignored_without_the_flag(feed_router, isolated_feed_cache):
    """The failure this whole design exists to prevent.

    An empty cache proves nothing here — the network path would be taken for
    want of an alternative. The copies have to exist and still be ignored, and
    the run has to return what the feed says *now*, not what it said before.
    """
    feed_router("alpha.", lambda url: FakeParsed([entry("yesterday's story")]))
    ingest_articles(make_config(["Alpha"]))
    assert isolated_feed_cache.glob("*.json")

    feed_router("alpha.", lambda url: FakeParsed([entry("this morning's story")]))
    feed_router.calls.clear()

    articles = ingest_articles(make_config(["Alpha"]))

    assert len(feed_router.calls) == 1, "a stored copy suppressed the fetch"
    assert [a.title for a in articles] == ["this morning's story"]


def test_a_refetch_overwrites_the_stored_copy(feed_router, isolated_feed_cache):
    """Otherwise --cached-feeds would keep serving the first run forever."""
    feed_router("alpha.", lambda url: FakeParsed([entry("first")]))
    ingest_articles(make_config(["Alpha"]))

    feed_router("alpha.", lambda url: FakeParsed([entry("second")]))
    ingest_articles(make_config(["Alpha"]))

    from_cache = ingest_articles(make_config(["Alpha"]), use_cached=True)
    assert [a.title for a in from_cache] == ["second"]


def test_every_run_leaves_a_copy_behind(feed_router, isolated_feed_cache):
    """Writing is unconditional; that's what makes the flag useful later."""
    _install(feed_router)

    ingest_articles(make_config(FEEDS))

    assert len(list(isolated_feed_cache.glob("*.json"))) == len(FEEDS)


def test_with_the_flag_nothing_is_fetched(feed_router, isolated_feed_cache):
    _install(feed_router)
    ingest_articles(make_config(FEEDS))
    feed_router.calls.clear()

    articles = ingest_articles(make_config(FEEDS), use_cached=True)

    assert feed_router.calls == [], "--cached-feeds went to the network"
    assert len(articles) == 2 * len(FEEDS)


def test_cached_and_live_runs_agree(feed_router, isolated_feed_cache):
    """Given the same feed data, the cached path is indistinguishable.

    Compared through dedup rather than a rendered digest because everything
    past dedup needs the embeddings API; ingest and dedup are the only stages
    the cache can affect.
    """
    _install(feed_router)
    live = dedup_articles(ingest_articles(make_config(FEEDS)))
    cached = dedup_articles(ingest_articles(make_config(FEEDS), use_cached=True))

    def shape(articles):
        return [
            (a.source, a.title, a.url, a.published, a.summary, a.theme)
            for a in articles
        ]

    assert shape(cached) == shape(live)


def test_a_stale_cache_warns(feed_router, isolated_feed_cache, caplog):
    _install(feed_router)
    ingest_articles(make_config(FEEDS))
    _age_cache(isolated_feed_cache, STALE_AFTER_HOURS + 3)

    with caplog.at_level(logging.INFO, logger="esp_news.nodes.ingest"):
        ingest_articles(make_config(FEEDS), use_cached=True)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("STALE CACHE" in w for w in warnings), warnings
    assert any(f"{len(FEEDS)} cached feeds are older" in w for w in warnings)


def test_a_fresh_cache_does_not_warn(feed_router, isolated_feed_cache, caplog):
    _install(feed_router)
    ingest_articles(make_config(FEEDS))

    with caplog.at_level(logging.INFO, logger="esp_news.nodes.ingest"):
        ingest_articles(make_config(FEEDS), use_cached=True)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("STALE CACHE" in w for w in warnings), warnings


def test_a_missing_copy_is_reported_not_fetched(feed_router, isolated_feed_cache, caplog):
    _install(feed_router)

    with caplog.at_level(logging.INFO, logger="esp_news.nodes.ingest"):
        articles = ingest_articles(make_config(FEEDS), use_cached=True)

    assert articles == []
    assert feed_router.calls == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("no stored copies found" in w for w in warnings), warnings


def test_the_cache_serves_a_wider_lookback_than_it_was_written_with(
    feed_router, isolated_feed_cache
):
    """The entries are stored unfiltered, so --hours can change afterwards."""
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    feed_router(
        "alpha.",
        lambda url: FakeParsed([entry("old story", when=old),
                                entry("recent story", when=recent)]),
    )

    narrow = make_config(["Alpha"])
    narrow.settings.lookback_hours = 6
    written = ingest_articles(narrow)
    assert [a.title for a in written] == ["recent story"]

    wide = make_config(["Alpha"])
    wide.settings.lookback_hours = 48
    from_cache = ingest_articles(wide, use_cached=True)

    assert [a.title for a in from_cache] == ["old story", "recent story"]


def test_an_http_error_is_not_stored(feed_router, isolated_feed_cache):
    """A 429 must not become a permanently empty cached feed."""
    feed_router("alpha.", lambda url: FakeParsed([entry("good")], status=200))
    feed_router("beta.", lambda url: FakeParsed([], status=429))

    ingest_articles(make_config(["Alpha", "Beta"]))

    cache = FeedCache(isolated_feed_cache)
    assert cache.read("https://alpha.example.test/rss") is not None
    assert cache.read("https://beta.example.test/rss") is None


def test_a_raised_fetch_is_not_stored(feed_router, isolated_feed_cache):
    def explode(url):
        raise RuntimeError("connection reset")

    feed_router("alpha.", explode)
    ingest_articles(make_config(["Alpha"]))

    assert FeedCache(isolated_feed_cache).read("https://alpha.example.test/rss") is None


class TestFeedCacheUnit:
    def test_round_trips_entries(self, tmp_path):
        cache = FeedCache(tmp_path)
        entries = [{"title": "t", "url": "u", "summary": "s", "published": None}]
        cache.write("https://x.test/rss", entries, name="X")

        stored = cache.read("https://x.test/rss")

        assert stored is not None
        assert stored.entries == entries
        assert stored.age_hours < 1

    def test_unknown_url_is_a_miss(self, tmp_path):
        assert FeedCache(tmp_path).read("https://nope.test/rss") is None

    def test_corrupt_file_is_a_miss_not_a_crash(self, tmp_path):
        cache = FeedCache(tmp_path)
        cache.write("https://x.test/rss", [], name="X")
        cache.path_for("https://x.test/rss").write_text("{ not json")

        assert cache.read("https://x.test/rss") is None

    def test_a_version_bump_invalidates_old_files(self, tmp_path):
        cache = FeedCache(tmp_path)
        cache.write("https://x.test/rss", [{"title": "t"}], name="X")
        path = cache.path_for("https://x.test/rss")
        payload = json.loads(path.read_text())
        payload["version"] = 999
        path.write_text(json.dumps(payload))

        assert cache.read("https://x.test/rss") is None

    def test_no_cache_dir_disables_it(self):
        cache = FeedCache(None)
        cache.write("https://x.test/rss", [{"title": "t"}])
        assert cache.read("https://x.test/rss") is None

    @pytest.mark.parametrize(
        "hours,stale", [(0.5, False), (STALE_AFTER_HOURS - 0.1, False),
                        (STALE_AFTER_HOURS + 0.1, True)]
    )
    def test_staleness_boundary(self, hours, stale):
        when = datetime.now(timezone.utc) - timedelta(hours=hours)
        assert CachedFeed(entries=[], fetched_at=when).is_stale is stale
