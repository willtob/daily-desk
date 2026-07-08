"""Load and validate feeds.yaml into typed config objects."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# feeds.yaml lives at the repo root: src/esp_news/config.py -> parents[2].
DEFAULT_FEEDS_PATH = Path(__file__).resolve().parents[2] / "feeds.yaml"


class FeedSource(BaseModel):
    """A single RSS feed, tagged with the theme it belongs to."""

    name: str
    url: str
    theme: str


class Settings(BaseModel):
    """Run-time settings read from the ``settings`` block of feeds.yaml."""

    lookback_hours: int = 48


class FeedsConfig(BaseModel):
    settings: Settings = Field(default_factory=Settings)
    sources: list[FeedSource] = Field(default_factory=list)


def load_feeds_config(path: str | Path | None = None) -> FeedsConfig:
    """Read feeds.yaml and flatten the ``themes`` mapping into a flat list of
    :class:`FeedSource` objects, each tagged with its theme."""
    path = Path(path) if path else DEFAULT_FEEDS_PATH
    if not path.exists():
        raise FileNotFoundError(f"feeds config not found: {path}")

    data = yaml.safe_load(path.read_text()) or {}
    settings = Settings(**(data.get("settings") or {}))

    sources: list[FeedSource] = []
    for theme, feeds in (data.get("themes") or {}).items():
        for feed in feeds or []:
            sources.append(FeedSource(name=feed["name"], url=feed["url"], theme=theme))

    return FeedsConfig(settings=settings, sources=sources)
