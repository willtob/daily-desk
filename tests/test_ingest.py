"""Ingest node — the guarantees the thread pool must not break.

Ordering is the one with teeth. Dedup sorts by published date and breaks ties on
insertion order, so if ingest returned feeds in completion order instead of
config order, the pipeline would still run, still produce a digest, and quietly
keep the wrong copy of a duplicated story. Nothing downstream would notice, so
it gets pinned here.
"""

from __future__ import annotations

import logging

import pytest

from esp_news.nodes.ingest import _host_locks, _host_of, ingest_articles
from tests.conftest import FakeParsed, entry, make_config


def test_articles_come_back_in_feed_order(slow_feeds):
    """Feed order, not completion order — even when feed 1 is the slowest."""
    names = [f"Feed{i}" for i in range(12)]
    slow_feeds(names)

    articles = ingest_articles(make_config(names))

    assert [a.source for a in articles] == [n for n in names for _ in range(2)]
    assert [a.title for a in articles] == [
        f"{n}-{suffix}" for n in names for suffix in ("a", "b")
    ]


def test_one_failing_feed_keeps_the_rest(feed_router):
    """A feed that raises costs its own articles and nothing else."""
    names = [f"Feed{i}" for i in range(37)]
    for name in names:
        feed_router(
            f"{name.lower()}.",
            lambda url, name=name: FakeParsed([entry(f"{name}-a")]),
        )

    def explode(url):
        raise RuntimeError("connection reset")

    feed_router("feed13.", explode)

    articles = ingest_articles(make_config(names))

    assert len(articles) == 36
    assert "Feed13" not in {a.source for a in articles}
    # The survivors are still in config order, with the gap closed.
    assert [a.source for a in articles] == [n for n in names if n != "Feed13"]


def test_failing_feed_is_logged_as_an_error(feed_router, caplog):
    names = ["Alpha", "Beta"]
    feed_router("alpha.", lambda url: FakeParsed([entry("a")]))

    def explode(url):
        raise RuntimeError("connection reset")

    feed_router("beta.", explode)

    with caplog.at_level(logging.INFO, logger="esp_news.nodes.ingest"):
        ingest_articles(make_config(names))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Beta" in errors[0].getMessage()
    assert "connection reset" in errors[0].getMessage()


def test_replayed_logs_stay_grouped_per_feed(feed_router, caplog):
    """Each feed's warning sits immediately before that feed's count line.

    Emitted from the workers these would interleave across feeds, which is the
    whole reason they are buffered and replayed.
    """
    names = [f"Feed{i}" for i in range(8)]
    for i, name in enumerate(names):
        feed_router(
            f"{name.lower()}.",
            # Every feed is bozo, so a warning and a count line compete for
            # adjacency across all eight.
            lambda url, name=name: FakeParsed(
                [entry(f"{name}-a")],
                bozo=True,
                bozo_exception=f"{name} is malformed",
            ),
        )

    with caplog.at_level(logging.INFO, logger="esp_news.nodes.ingest"):
        ingest_articles(make_config(names))

    messages = [r.getMessage() for r in caplog.records]
    per_feed = [m for m in messages if any(n in m for n in names)]

    # Two lines per feed, in feed order, warning first.
    assert len(per_feed) == 2 * len(names)
    for i, name in enumerate(names):
        warning, count = per_feed[2 * i], per_feed[2 * i + 1]
        assert f"{name} is malformed" in warning
        assert count.strip().startswith(name)
        assert "1 articles" in count


def test_http_error_is_not_logged_as_an_empty_feed(feed_router, caplog):
    """A 429 must be distinguishable from a feed that published nothing."""
    feed_router("ratelimited.", lambda url: FakeParsed([], status=429))
    feed_router("quiet.", lambda url: FakeParsed([], status=200))

    with caplog.at_level(logging.INFO, logger="esp_news.nodes.ingest"):
        ingest_articles(make_config(["RateLimited", "Quiet"]))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "RateLimited" in warnings[0]
    assert "429" in warnings[0]
    assert "Quiet" not in " ".join(warnings)


def test_undated_articles_are_kept_and_counted(feed_router, caplog):
    feed_router(
        "mixed.",
        lambda url: FakeParsed([entry("dated"), entry("undated", when=None)]),
    )

    with caplog.at_level(logging.INFO, logger="esp_news.nodes.ingest"):
        articles = ingest_articles(make_config(["Mixed"]))

    assert len(articles) == 2
    assert any("1 undated, kept" in r.getMessage() for r in caplog.records)


def test_stale_articles_are_dropped(feed_router):
    from datetime import datetime, timedelta, timezone

    old = datetime.now(timezone.utc) - timedelta(days=30)
    feed_router("mixed.", lambda url: FakeParsed([entry("fresh"), entry("old", when=old)]))

    articles = ingest_articles(make_config(["Mixed"]))

    assert [a.title for a in articles] == ["fresh"]


class TestHostLimiting:
    """Feeds sharing a host must not be fetched at the same time."""

    def test_same_host_feeds_share_one_lock(self):
        config = make_config(["A", "B"])
        config.sources[0].url = "https://www.lavanguardia.com/rss/comer.xml"
        config.sources[1].url = "https://www.lavanguardia.com/rss/local/barcelona.xml"

        locks = _host_locks(config.sources)

        assert len(locks) == 1
        assert locks[_host_of(config.sources[0].url)] is locks[
            _host_of(config.sources[1].url)
        ]

    def test_distinct_hosts_get_distinct_locks(self):
        config = make_config(["A", "B"])
        locks = _host_locks(config.sources)
        assert len(locks) == 2

    def test_same_host_fetches_never_overlap(self, feed_router):
        """Two feeds on one host, six workers, no concurrent entry."""
        import threading
        import time

        active = 0
        overlaps = []
        guard = threading.Lock()

        def handler(url):
            nonlocal active
            with guard:
                active += 1
                if active > 1:
                    overlaps.append(url)
            time.sleep(0.05)
            with guard:
                active -= 1
            return FakeParsed([entry("x")])

        feed_router("shared.host", handler)

        config = make_config([f"F{i}" for i in range(4)])
        for source in config.sources:
            source.url = f"https://shared.host/{source.name}.xml"

        ingest_articles(config)

        assert overlaps == []

    def test_different_hosts_still_run_concurrently(self, feed_router):
        """The host lock must not accidentally serialise the whole pool."""
        import time

        def handler(url):
            time.sleep(0.2)
            return FakeParsed([entry("x")])

        names = [f"Feed{i}" for i in range(6)]
        for name in names:
            feed_router(f"{name.lower()}.", handler)

        start = time.monotonic()
        ingest_articles(make_config(names))
        elapsed = time.monotonic() - start

        # Six 0.2s feeds on six workers: ~0.2s parallel, 1.2s serial.
        assert elapsed < 0.7, f"feeds appear to be serialised ({elapsed:.2f}s)"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://WWW.Example.COM/rss", "www.example.com"),
        ("http://export.arxiv.org/api/query?search_query=all:%22x%22", "export.arxiv.org"),
    ],
)
def test_host_of_normalises(url, expected):
    assert _host_of(url) == expected
