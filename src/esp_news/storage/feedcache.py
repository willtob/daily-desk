"""Phase 1 cache — the last fetched copy of each feed, for tuning reruns.

Tuning ``interests.yaml`` only changes scoring, but scoring sits behind ingest,
so every rerun re-fetches all forty-odd feeds to reach the stage being changed.
That is the slowest part of the loop and the part that gets outlets to start
returning 429s, because a tuning session hits them every few seconds.

Reading this cache is opt-in (``esp-digest --cached-feeds``) and writing is not.
The asymmetry is the whole design: a real morning digest must never quietly
serve yesterday's news because a cache file happened to exist, but it costs
nothing for that run to leave a copy behind for the tuning session afterwards.

What is stored is the feed's entries *before* the lookback-cutoff filter, which
is what makes the cache safe to reuse across different ``--hours`` values. Store
the filtered result instead and a cached ``--hours 72`` run would silently
return only the articles that survived some earlier ``--hours 12``.

Layout follows ``.cache/pages/``: one file per feed keyed by a hash of its URL,
written to a temp name and renamed into place, so an interrupted run leaves
whole files rather than a half-written index.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# .cache/feeds/ — src/esp_news/storage/feedcache.py -> parents[3].
DEFAULT_FEED_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "feeds"

# Past this, a cached copy is old enough that the digest built from it is
# describing a different morning. Not an expiry — the run still proceeds, loudly
# — because a tuning session on stale articles is still a useful tuning session.
STALE_AFTER_HOURS = 6

# Bump when the stored entry shape changes, so old files are ignored rather than
# read as though they carried fields they never had.
CACHE_VERSION = 1


@dataclass(frozen=True)
class CachedFeed:
    """One feed's entries as they were at ``fetched_at``, before any filtering."""

    entries: list[dict]
    fetched_at: datetime

    @property
    def age_hours(self) -> float:
        return (datetime.now(timezone.utc) - self.fetched_at).total_seconds() / 3600.0

    @property
    def is_stale(self) -> bool:
        return self.age_hours > STALE_AFTER_HOURS


class FeedCache:
    """Per-feed snapshots on disk, keyed by feed URL."""

    def __init__(self, cache_dir: str | Path | None = DEFAULT_FEED_CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def path_for(self, url: str) -> Path | None:
        if not self.cache_dir or not url:
            return None
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    def read(self, url: str) -> CachedFeed | None:
        """Return the stored snapshot, or ``None`` if there isn't a usable one.

        Never raises: an unreadable or outdated cache file is a cache miss, and
        the caller already has to handle a feed it has no copy of.
        """
        path = self.path_for(url)
        if not path or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
            if payload.get("version") != CACHE_VERSION:
                return None
            return CachedFeed(
                entries=payload["entries"],
                fetched_at=datetime.fromisoformat(payload["fetched_at"]),
            )
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Ignoring unreadable feed cache %s: %s", path, exc)
            return None

    def write(self, url: str, entries: list[dict], *, name: str = "") -> None:
        """Store ``entries`` for ``url``. Failures are logged, never raised."""
        path = self.path_for(url)
        if not path:
            return
        payload = {
            "version": CACHE_VERSION,
            "url": url,
            "name": name,  # human-readable, so the cache dir can be read by eye
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "entries": entries,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False))
            tmp.replace(path)
        except OSError as exc:
            logger.warning("Could not write feed cache %s: %s", path, exc)
