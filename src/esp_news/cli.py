"""Command-line entry points for running pipeline nodes standalone."""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from esp_news.config import load_feeds_config
from esp_news.nodes.ingest import ingest_articles
from esp_news.tracing import init_tracing


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def ingest_main() -> None:
    """Phase 1 checkpoint: fetch feeds and print per-source / per-theme counts."""
    parser = argparse.ArgumentParser(
        description="Phase 1 — fetch and filter RSS articles."
    )
    parser.add_argument(
        "--feeds", default=None, help="Path to feeds.yaml (default: repo root)."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=None,
        help="Override the lookback window (hours) from feeds.yaml.",
    )
    args = parser.parse_args()

    _configure_logging()
    init_tracing()

    config = load_feeds_config(args.feeds)
    if args.hours is not None:
        config.settings.lookback_hours = args.hours

    articles = ingest_articles(config)

    print("\n=== Ingest summary ===")
    by_source = Counter(a.source for a in articles)
    for source, count in by_source.most_common():
        print(f"  {source:<26} {count:>3}")
    print("  " + "-" * 32)
    for theme, count in sorted(Counter(a.theme for a in articles).items()):
        print(f"  [{theme}] {count}")
    print(f"\n  TOTAL: {len(articles)} articles from {len(config.sources)} feeds")

    dated = [a.published for a in articles if a.published]
    if dated:
        print(f"  Date range: {min(dated).date()} -> {max(dated).date()}")


if __name__ == "__main__":
    ingest_main()
