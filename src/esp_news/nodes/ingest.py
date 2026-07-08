"""Phase 1 — ingest node.

Fetch each configured RSS feed, parse entries into :class:`Article` objects, and
keep those published within the configured lookback window. HTML stripping and
dedup are deliberately deferred to Phase 2.
"""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timedelta, timezone
from time import struct_time

import feedparser
from langsmith import traceable

from esp_news.config import FeedSource, FeedsConfig
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


def _to_datetime(parsed: struct_time | None) -> datetime | None:
    """Convert a feedparser time struct (already normalized to UTC) to an
    aware ``datetime``."""
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


@traceable(run_type="tool", name="fetch_feed")
def _fetch_source(source: FeedSource, cutoff: datetime) -> list[Article]:
    """Fetch and parse a single feed, returning articles newer than ``cutoff``.

    Articles with no usable date are kept (we can't judge their age), which is
    rare for the major feeds but shouldn't silently drop content.
    """
    parsed = feedparser.parse(source.url, agent=_USER_AGENT)
    if parsed.bozo:
        logger.warning(
            "Feed parse issue for %s (%s): %s",
            source.name,
            source.url,
            parsed.get("bozo_exception"),
        )

    articles: list[Article] = []
    undated = 0
    for entry in parsed.entries:
        published = _to_datetime(entry.get("published_parsed")) or _to_datetime(
            entry.get("updated_parsed")
        )
        if published is None:
            undated += 1
        elif published < cutoff:
            continue
        articles.append(
            Article(
                title=entry.get("title", "(untitled)").strip(),
                url=entry.get("link", ""),
                source=source.name,
                theme=source.theme,
                published=published,
                summary=entry.get("summary", ""),
            )
        )

    logger.info(
        "  %-26s %3d articles%s",
        source.name,
        len(articles),
        f" ({undated} undated, kept)" if undated else "",
    )
    return articles


@traceable(run_type="chain", name="ingest")
def ingest_articles(config: FeedsConfig) -> list[Article]:
    """Fetch every configured feed and return recent articles across all sources.

    A single failing feed is logged and skipped rather than aborting the run.
    """
    socket.setdefaulttimeout(_FETCH_TIMEOUT_SECONDS)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.settings.lookback_hours)
    logger.info(
        "Ingesting %d feeds (lookback=%dh, cutoff=%s)",
        len(config.sources),
        config.settings.lookback_hours,
        cutoff.isoformat(),
    )

    articles: list[Article] = []
    for source in config.sources:
        try:
            articles.extend(_fetch_source(source, cutoff))
        except Exception as exc:  # noqa: BLE001 - one bad feed shouldn't kill the run
            logger.error("Failed to fetch %s (%s): %s", source.name, source.url, exc)

    logger.info(
        "Ingested %d total articles from %d feeds", len(articles), len(config.sources)
    )
    return articles
