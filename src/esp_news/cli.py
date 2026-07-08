"""Command-line entry points for running pipeline nodes standalone."""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from esp_news.config import FeedsConfig, load_feeds_config
from esp_news.models import Article
from esp_news.nodes.dedup import dedup_articles
from esp_news.nodes.ingest import ingest_articles
from esp_news.tracing import init_tracing


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _base_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--feeds", default=None, help="Path to feeds.yaml (default: repo root)."
    )
    parser.add_argument(
        "--hours", type=int, default=None, help="Override the lookback window (hours)."
    )
    return parser


def _load_and_ingest(args: argparse.Namespace) -> tuple[list[Article], FeedsConfig]:
    config = load_feeds_config(args.feeds)
    if args.hours is not None:
        config.settings.lookback_hours = args.hours
    return ingest_articles(config), config


def _print_summary(articles: list[Article]) -> None:
    """Per-source and per-theme counts for a set of articles."""
    for source, count in Counter(a.source for a in articles).most_common():
        print(f"  {source:<26} {count:>3}")
    print("  " + "-" * 32)
    for theme, count in sorted(Counter(a.theme for a in articles).items()):
        print(f"  [{theme}] {count}")


def ingest_main() -> None:
    """Phase 1 checkpoint: fetch feeds and print per-source / per-theme counts."""
    args = _base_parser("Phase 1 — fetch and filter RSS articles.").parse_args()

    _configure_logging()
    init_tracing()

    articles, config = _load_and_ingest(args)

    print("\n=== Ingest summary ===")
    _print_summary(articles)
    print(f"\n  TOTAL: {len(articles)} articles from {len(config.sources)} feeds")
    dated = [a.published for a in articles if a.published]
    if dated:
        print(f"  Date range: {min(dated).date()} -> {max(dated).date()}")


def dedup_main() -> None:
    """Phase 2 checkpoint: ingest, then normalize + collapse duplicates."""
    parser = _base_parser("Phase 2 — normalize and dedup articles.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="Title-similarity threshold for near-dup merging (0-1, default 0.6).",
    )
    args = parser.parse_args()

    _configure_logging()
    init_tracing()

    raw, _ = _load_and_ingest(args)
    deduped = dedup_articles(raw, similarity_threshold=args.threshold)

    print("\n=== Dedup summary ===")
    print(f"  raw:     {len(raw)}")
    print(f"  deduped: {len(deduped)}  (removed {len(raw) - len(deduped)})")
    print()
    _print_summary(deduped)

    sample = next((a for a in deduped if a.summary), None)
    if sample:
        print("\n  sample cleaned summary (HTML stripped):")
        print(f"    [{sample.source}] {sample.title[:70]}")
        print(f"    {sample.summary[:180]}...")


if __name__ == "__main__":
    ingest_main()
