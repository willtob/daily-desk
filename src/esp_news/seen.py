"""Cross-run memory of which articles have already been shown.

Phase 2 dedups *within* a run. This handles the other half: "just don't show
me the same article a bunch of times" across runs — which matters more once
evergreen articles are in scope, because a high-scoring piece would otherwise
top the digest every day until it aged out of the lookback window.

Keyed by canonical URL (the same normalization dedup uses), so a link that
picks up tracking params between runs still counts as seen.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from esp_news.nodes.dedup import _canonical_url

logger = logging.getLogger(__name__)

# Sits next to the generated digests: src/esp_news/seen.py -> parents[2].
DEFAULT_SEEN_PATH = Path(__file__).resolve().parents[2] / "digests" / "seen.json"

# How long an article stays "seen". Longer than any sane lookback window, but
# short enough that the file can't grow without bound.
DEFAULT_RETENTION_DAYS = 45


class SeenStore:
    """A URL -> first-shown-date map, persisted as JSON."""

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

    def contains(self, url: str) -> bool:
        key = _canonical_url(url)
        return bool(key) and key in self._seen

    def add(self, url: str, when: date | None = None) -> None:
        key = _canonical_url(url)
        if not key:
            return   # no URL to key on; can't track it, so never suppress it
        self._seen.setdefault(key, (when or date.today()).isoformat())

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
