"""Phase 1 — ingest node.

Fetch each configured RSS feed, parse entries into :class:`Article` objects, and
keep those published within the configured lookback window. HTML stripping and
dedup are deliberately deferred to Phase 2.

Feeds are fetched concurrently, for the same reason ``extract.py`` fetches pages
concurrently: this is the one stage with no cache, so every run pays the full
network cost, and forty-odd feeds at up to fifteen seconds each dominates the
wall clock. The concurrency is deliberately kept boring — results come back in
feed order, and each feed's log lines are replayed afterwards rather than
emitted from the worker, so a parallel run reads exactly like a serial one.

What serial fetching gave away for free was pacing, so a per-host lock keeps it:
no host is ever asked for two feeds at once. That covers the hosts this config
hits twice (arXiv, La Vanguardia). It does not cover a host being asked once per
run by runs that now finish five times faster — a feed that rate-limits on that
is why every HTTP >= 400 gets its own log line below.

Fetching and filtering are separate steps here because of ``feedcache``: every
run stores what a feed returned, unfiltered, and a tuning run can be told to
read those copies instead of the network. Applying the lookback cutoff after the
cache, never before, is what lets one stored copy serve any ``--hours``.
"""

from __future__ import annotations

import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
from time import struct_time
from typing import NamedTuple
from urllib.parse import urlparse

import feedparser
from langsmith import traceable

from esp_news.config import FeedSource, FeedsConfig
from esp_news.feedcache import DEFAULT_FEED_CACHE_DIR, STALE_AFTER_HOURS, FeedCache
from esp_news.models import Article

logger = logging.getLogger(__name__)

# Browser-ish UA so major outlets don't 403 a bare feedparser request, tagged so
# it's honest about what's hitting the feed.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) esp-news-reporter/0.1"
)
# Don't let a slow/hanging feed stall the whole run.
_FETCH_TIMEOUT_SECONDS = 15
# Matches the page fetcher: enough to hide the slow feeds behind the fast ones,
# few enough to stay a polite number of simultaneous strangers on the network.
_MAX_WORKERS = 6

# A log record a feed produced, held until the pool drains: (level, msg, args).
# Buffered rather than logged in the worker because six threads writing the
# aligned per-feed list interleave it into nonsense.
_LogLine = tuple[int, str, tuple]


class _FeedOutcome(NamedTuple):
    """One feed's result, before the lookback cutoff is applied.

    ``cacheable`` is false for anything that shouldn't be stored and served
    later — an HTTP error or a raised exception. Caching those would turn a
    momentary 429 into a feed that stays empty for every cached run afterwards.
    """

    entries: list[dict]
    logs: list[_LogLine]
    cacheable: bool = False
    cached_age_hours: float | None = None


def _host_of(url: str) -> str:
    """The host a feed URL will be fetched from, for per-host serialisation."""
    return urlparse(url).netloc.lower()


def _host_locks(sources: list[FeedSource]) -> dict[str, threading.Lock]:
    """One lock per distinct host, so no host sees two simultaneous requests.

    Serial fetching used to pace requests for free: one at a time, a couple of
    seconds apart. The pool removed that pacing, and two of these feeds are
    arXiv queries while another two are La Vanguardia sections — hosts that
    would otherwise get hit twice at once by a run that is trying to be a good
    citizen. Built here rather than in a module-level ``defaultdict`` because
    populating one from six threads is itself a race.
    """
    locks: dict[str, threading.Lock] = {}
    for source in sources:
        locks.setdefault(_host_of(source.url), threading.Lock())
    return locks


def _to_datetime(parsed: struct_time | None) -> datetime | None:
    """Convert a feedparser time struct (already normalized to UTC) to an
    aware ``datetime``."""
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def _trace_entries_only(outputs: object) -> object:
    """Keep the buffered log lines out of the LangSmith trace.

    ``_fetch_source`` returns its log lines alongside its entries so the caller
    can order them, but the logs are plumbing and recording them makes the trace
    harder to read. ``process_outputs`` rewrites what gets stored, not what the
    caller receives. Anything that isn't the expected shape is passed through:
    on an exception LangSmith calls this with ``None``, and unpacking that would
    only trade a noisy output for a noisy warning.
    """
    if not isinstance(outputs, _FeedOutcome):
        return outputs
    return {"entries": outputs.entries}


def _entry_record(entry: dict) -> dict:
    """Flatten a feedparser entry to the fields the pipeline actually reads.

    Stored in the feed cache as-is, so it holds plain JSON types — a resolved
    ISO timestamp rather than feedparser's ``struct_time``.
    """
    published = _to_datetime(entry.get("published_parsed")) or _to_datetime(
        entry.get("updated_parsed")
    )
    return {
        "title": entry.get("title", "(untitled)").strip(),
        "url": entry.get("link", ""),
        "summary": entry.get("summary", ""),
        "published": published.isoformat() if published else None,
    }


@traceable(run_type="tool", name="fetch_feed", process_outputs=_trace_entries_only)
def _fetch_source(source: FeedSource) -> _FeedOutcome:
    """Fetch and parse a single feed into unfiltered entry records.

    Deliberately does no date filtering: the cutoff depends on ``--hours``,
    which the cache must not bake in. See the module docstring.

    Hands its log lines back to the caller instead of emitting them, so the
    per-feed output stays in feed order when several feeds are in flight.
    """
    logs: list[_LogLine] = []
    parsed = feedparser.parse(source.url, agent=_USER_AGENT)

    # A 429 or a 403 parses cleanly into zero entries, so without this line an
    # outlet that rate-limited us is indistinguishable in the log from an outlet
    # that simply published nothing today — and a profile tuned against a run
    # with a feed silently missing is tuned against the wrong data.
    status = parsed.get("status")
    http_error = status is not None and status >= 400
    if http_error:
        logs.append(
            (
                logging.WARNING,
                "  %-26s HTTP %d — feed unavailable, not empty",
                (source.name, status),
            )
        )

    if parsed.bozo:
        logs.append(
            (
                logging.WARNING,
                "Feed parse issue for %s (%s): %s",
                (source.name, source.url, parsed.get("bozo_exception")),
            )
        )

    entries = [_entry_record(e) for e in parsed.entries]
    return _FeedOutcome(entries=entries, logs=logs, cacheable=not http_error)


def _fetch_safely(
    source: FeedSource, host_lock: threading.Lock, cache: FeedCache | None
) -> _FeedOutcome:
    """Run one feed's fetch, turning any failure into an empty result.

    The catch lives here rather than around the pool because
    ``ThreadPoolExecutor.map`` re-raises at the point of iteration: a single bad
    feed escaping the worker would abort the whole ingest, and the remaining
    feeds' results would be thrown away with it.

    ``host_lock`` holds same-host fetches to one at a time. It is taken inside
    the worker, so a feed waiting on a busy host occupies its pool slot rather
    than stalling the queue — with two feeds per host at most, that wait is one
    fetch long.

    The cache write happens here, in the worker, for the same reason the fetch
    does: it is per-feed I/O, and one feed's disk write has no business holding
    up another feed's network call.
    """
    try:
        with host_lock:
            outcome = _fetch_source(source)
    except Exception as exc:  # noqa: BLE001 - one bad feed shouldn't kill the run
        return _FeedOutcome(
            entries=[],
            logs=[
                (
                    logging.ERROR,
                    "Failed to fetch %s (%s): %s",
                    (source.name, source.url, exc),
                )
            ],
        )

    if cache is not None and outcome.cacheable:
        cache.write(source.url, outcome.entries, name=source.name)
    return outcome


def _read_cached(source: FeedSource, cache: FeedCache) -> _FeedOutcome:
    """Serve one feed from its stored copy, or report that there isn't one.

    A miss is not an error and is not a reason to reach for the network: the
    flag's promise is that the run stays offline, so a feed with no stored copy
    contributes nothing and says so.
    """
    cached = cache.read(source.url)
    if cached is None:
        return _FeedOutcome(
            entries=[],
            logs=[
                (
                    logging.WARNING,
                    "  %-26s no cached copy — run once without --cached-feeds",
                    (source.name,),
                )
            ],
        )
    return _FeedOutcome(
        entries=cached.entries, logs=[], cached_age_hours=cached.age_hours
    )


def _articles_since(
    entries: list[dict], source: FeedSource, cutoff: datetime
) -> tuple[list[Article], int]:
    """Turn stored entry records into Articles published since ``cutoff``.

    Entries with no usable date are kept (we can't judge their age), which is
    rare for the major feeds but shouldn't silently drop content. Returns the
    articles and how many of them were undated.
    """
    articles: list[Article] = []
    undated = 0
    for record in entries:
        raw = record.get("published")
        published = datetime.fromisoformat(raw) if raw else None
        if published is None:
            undated += 1
        elif published < cutoff:
            continue
        articles.append(
            Article(
                title=record.get("title", "(untitled)"),
                url=record.get("url", ""),
                source=source.name,
                theme=source.theme,
                published=published,
                summary=record.get("summary", ""),
            )
        )
    return articles, undated


def _fetch_all(
    config: FeedsConfig, cache: FeedCache | None
) -> list[_FeedOutcome]:
    """Fetch every feed concurrently, in feed order."""
    # One context copy per feed, all taken here in the calling thread, exactly as
    # ArticleFetcher.fetch_many does it: a Context cannot be entered by two
    # threads at once, and copying inside the worker would capture the worker's
    # context instead of this one — which is what keeps LangSmith's current run
    # as the parent, rather than filing every fetch as an orphaned root.
    contexts = [copy_context() for _ in config.sources]
    locks = _host_locks(config.sources)
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        # pool.map, not as_completed: dedup sorts by published date and breaks
        # ties on insertion order, so a shuffled ingest would quietly change
        # which of two duplicates survives.
        return list(
            pool.map(
                lambda c, s: c.run(_fetch_safely, s, locks[_host_of(s.url)], cache),
                contexts,
                config.sources,
            )
        )


def _warn_if_stale(results: list[_FeedOutcome], feed_count: int) -> None:
    """Say plainly, once, that this digest is built from stored copies."""
    ages = [r.cached_age_hours for r in results if r.cached_age_hours is not None]
    if not ages:
        logger.warning(
            "--cached-feeds: no stored copies found for any of %d feeds — "
            "run once without the flag to populate the cache",
            feed_count,
        )
        return

    stale = [a for a in ages if a > STALE_AFTER_HOURS]
    logger.info("Serving %d of %d feeds from cache (no network)", len(ages), feed_count)
    if stale:
        logger.warning(
            "STALE CACHE: %d of %d cached feeds are older than %dh "
            "(oldest %.1fh) — these articles are not today's news",
            len(stale),
            len(ages),
            STALE_AFTER_HOURS,
            max(ages),
        )


@traceable(run_type="chain", name="ingest")
def ingest_articles(
    config: FeedsConfig,
    *,
    cache: FeedCache | None = None,
    use_cached: bool = False,
) -> list[Article]:
    """Fetch every configured feed and return recent articles across all sources.

    A single failing feed is logged and skipped rather than aborting the run.

    ``use_cached`` serves the stored copies and makes no network requests at
    all; without it the feeds are fetched and the copies are refreshed.
    ``cache`` defaults to the standard location — pass ``FeedCache(None)`` to
    switch the cache off in both directions.
    """
    if cache is None:
        cache = FeedCache(DEFAULT_FEED_CACHE_DIR)

    # Process-global, and it still governs the sockets the worker threads open,
    # so setting it once here covers every feed in the pool.
    socket.setdefaulttimeout(_FETCH_TIMEOUT_SECONDS)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.settings.lookback_hours)
    logger.info(
        "Ingesting %d feeds (lookback=%dh, cutoff=%s)%s",
        len(config.sources),
        config.settings.lookback_hours,
        cutoff.isoformat(),
        " [cached]" if use_cached else "",
    )

    if use_cached:
        results = [_read_cached(source, cache) for source in config.sources]
        _warn_if_stale(results, len(config.sources))
    else:
        results = _fetch_all(config, cache)

    articles: list[Article] = []
    for source, outcome in zip(config.sources, results):
        for level, msg, args in outcome.logs:
            logger.log(level, msg, *args)
        feed_articles, undated = _articles_since(outcome.entries, source, cutoff)
        logger.info(
            "  %-26s %3d articles%s%s",
            source.name,
            len(feed_articles),
            f" ({undated} undated, kept)" if undated else "",
            ""
            if outcome.cached_age_hours is None
            else f" (cached {outcome.cached_age_hours:.1f}h ago)",
        )
        articles.extend(feed_articles)

    logger.info(
        "Ingested %d total articles from %d feeds", len(articles), len(config.sources)
    )
    return articles
