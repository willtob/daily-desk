"""Cross-run memory of which articles have already been shown.

Phase 2 dedups *within* a run. This handles the other half: "just don't show
me the same article a bunch of times" across runs — which matters more once
evergreen articles are in scope, because a high-scoring piece would otherwise
top the digest every day until it aged out of the lookback window.

Keyed by canonical URL (the same normalization dedup uses), so a link that
picks up tracking params between runs still counts as seen.

**A URL is not always one article.** Some sources publish a rolling page —
Time Out's "what to do this weekend", a paper's weekly agenda — at a fixed URL
that is re-dated every week with entirely new content. Suppressing those by URL
alone shows the first week's edition once and then hides the page for the whole
retention window, which is backwards. So the check is URL *and* date: an
article whose feed says it was published after the day it was last shown counts
as new again. The cost is that a source which bumps its publish date on a
copy-edit can re-show a story; requiring the republish to land on a later
calendar day is what keeps that to same-URL-different-day rather than any edit.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from esp_news.nodes.dedup import _canonical_url

logger = logging.getLogger(__name__)

# Sits next to the generated digests: src/esp_news/storage/seen.py -> parents[3].
DEFAULT_SEEN_PATH = Path(__file__).resolve().parents[3] / "digests" / "seen.json"

# How long an article stays "seen". Longer than any sane lookback window, but
# short enough that the file can't grow without bound.
DEFAULT_RETENTION_DAYS = 45


class SeenStore:
    """A URL -> last-shown-date map, persisted as JSON."""

    def __init__(self, path: str | Path | None = DEFAULT_SEEN_PATH) -> None:
        self.path = Path(path) if path else None
        self._seen: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path or not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable seen store %s: %s", self.path, exc)
            return {}

    def __len__(self) -> int:
        return len(self._seen)

    def contains(self, url: str, published: datetime | date | None = None) -> bool:
        """Whether this article has already been shown.

        ``published`` is the article's own publish date. Passing it is what
        distinguishes "the same story again" from "the same URL, republished" —
        see the module docstring. Omitting it falls back to matching on URL
        alone, which is the right answer for a caller that has no date and the
        old behaviour for everything else.
        """
        key = _canonical_url(url)
        if not key:
            return False
        shown_on = self._seen.get(key)
        if shown_on is None:
            return False
        if published is None:
            return True
        try:
            shown = datetime.fromisoformat(shown_on).date()
        except ValueError:
            return True   # unparseable date; prune() drops it on the next save

        # Ingest stores publish times as aware UTC while the shown-on dates are
        # local calendar days, so the UTC date has to be moved onto the local
        # clock first. Skipping that makes anything published in the local
        # evening land on "tomorrow" and re-qualify a day later, every day.
        pub = published.astimezone().date() if isinstance(published, datetime) else published
        # Strictly later, so an article shown on the day it was published is
        # not immediately eligible again.
        return pub <= shown

    def add(self, url: str, when: date | None = None) -> None:
        key = _canonical_url(url)
        if not key:
            return   # no URL to key on; can't track it, so never suppress it
        # Overwrite rather than setdefault: the date has to mean "last shown"
        # for contains() to compare a republish against it. It also re-arms the
        # retention window each time, which only matters for a URL that was
        # re-admitted — and there the clock should start again anyway.
        self._seen[key] = (when or date.today()).isoformat()

    def prune(self, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
        """Drop entries older than the retention window. Returns how many went."""
        cutoff = date.today() - timedelta(days=retention_days)
        stale = []
        for key, seen_on in self._seen.items():
            try:
                if datetime.fromisoformat(seen_on).date() < cutoff:
                    stale.append(key)
            except ValueError:
                stale.append(key)   # unparseable date — drop it
        for key in stale:
            del self._seen[key]
        return len(stale)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._seen, indent=0, sort_keys=True))
        tmp.replace(self.path)
        logger.info("Seen store: %d urls remembered (%s)", len(self._seen), self.path)
