"""Shared fixtures for the ingest tests.

Everything here mocks ``feedparser.parse``. The ingest node is almost entirely
network glue, so the parts worth pinning down — ordering, error isolation, log
grouping — are exactly the parts that a real fetch makes untestable: 42 live
feeds vary run to run, and the failure this suite exists to catch (a feed's
articles landing in the wrong place) is invisible against moving data.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from esp_news.config.feeds import FeedSource, FeedsConfig, Settings

RECENT = datetime.now(timezone.utc) - timedelta(hours=1)


class FakeEntry(dict):
    """A feedparser entry — a dict with ``.get``, which is all ingest uses."""


class FakeParsed:
    """The subset of a feedparser result the ingest node touches."""

    def __init__(self, entries=(), *, bozo=False, bozo_exception=None, status=200):
        self.entries = list(entries)
        self.bozo = bozo
        self._fields = {"status": status, "bozo_exception": bozo_exception}

    def get(self, key, default=None):
        return self._fields.get(key, default)


def entry(title: str, *, when: datetime | None = RECENT) -> FakeEntry:
    """One RSS entry, dated inside the lookback window by default."""
    e = FakeEntry(title=title, link=f"https://example.test/{title}", summary="body")
    if when is not None:
        e["published_parsed"] = when.timetuple()
    return e


def make_config(names: list[str], *, host: str = "example.test") -> FeedsConfig:
    """A config of ``names``, each feed on its own host unless told otherwise."""
    return FeedsConfig(
        settings=Settings(lookback_hours=48),
        sources=[
            FeedSource(name=n, url=f"https://{n.lower()}.{host}/rss", theme="t")
            for n in names
        ],
    )


@pytest.fixture(autouse=True)
def isolated_feed_cache(tmp_path, monkeypatch):
    """Point the feed cache at a temp dir for every test.

    Autouse because ingest writes a copy of each feed on every run by design —
    without this, running the suite would scribble fixture feeds into the real
    ``.cache/feeds/`` and a later ``--cached-feeds`` run would serve them.
    """
    cache_dir = tmp_path / "feeds"
    monkeypatch.setattr(
        "esp_news.nodes.ingest.DEFAULT_FEED_CACHE_DIR", cache_dir, raising=True
    )
    return cache_dir


@pytest.fixture
def feed_router(monkeypatch):
    """Route ``feedparser.parse`` to a per-URL handler the test installs.

    Returns a ``register(url_fragment, handler)`` callable; the handler is
    called with the URL and returns a :class:`FakeParsed` (or raises, to
    exercise the failure path).

    The most recent matching registration wins, so a test can install a default
    for every feed and then override one of them.
    """
    import feedparser

    routes: list[tuple[str, object]] = []
    calls: list[str] = []

    def register(fragment, handler):
        routes.append((fragment, handler))

    def fake_parse(url, agent=None):
        calls.append(url)
        for fragment, handler in reversed(routes):
            if fragment in url:
                return handler(url) if callable(handler) else handler
        return FakeParsed()

    monkeypatch.setattr(feedparser, "parse", fake_parse)
    register.calls = calls
    return register


@pytest.fixture
def slow_feeds(feed_router):
    """Make each feed's latency the inverse of its position.

    The first feed in the config becomes the slowest, so a pool that returned
    results as they completed would reverse the order. Any test that passes
    with this fixture is testing ordering, not luck.
    """

    def install(names: list[str], *, unit: float = 0.05):
        total = len(names)
        for i, name in enumerate(names):
            delay = (total - i) * unit

            def handler(url, name=name, delay=delay):
                time.sleep(delay)
                return FakeParsed([entry(f"{name}-a"), entry(f"{name}-b")])

            feed_router(f"{name.lower()}.", handler)

    return install
