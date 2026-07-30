"""Command-line entry points for running pipeline nodes standalone."""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from esp_news.config import FeedsConfig, load_feeds_config
from esp_news.embeddings import DEFAULT_CACHE_PATH, EmbeddingClient, MissingAPIKeyError
from esp_news.interests import load_interests_profile
from esp_news.models import Article
from esp_news.nodes.dedup import dedup_articles
from esp_news.nodes.ingest import ingest_articles
from esp_news.nodes.score import score_articles
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


def _add_threshold_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="Title-similarity threshold for near-dup merging (0-1, default 0.6).",
    )


def dedup_main() -> None:
    """Phase 2 checkpoint: ingest, then normalize + collapse duplicates."""
    parser = _base_parser("Phase 2 — normalize and dedup articles.")
    _add_threshold_arg(parser)
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


def _print_ranked(articles: list[Article], label: str) -> None:
    """Score / area / source / title table for a slice of ranked articles."""
    print(f"\n  {label}")
    for art in articles:
        print(
            f"    {art.score:.4f}  [{art.matched_area:<18}] "
            f"{art.source[:18]:<18} {art.title[:64]}"
        )


def score_main() -> None:
    """Phase 3 checkpoint: score a real batch and eyeball the top and bottom.

    Prints both ends deliberately — the profile is only working if the top looks
    interesting *and* the bottom looks like stuff I'd skip.
    """
    parser = _base_parser("Phase 3 — score articles against the interest profile.")
    _add_threshold_arg(parser)
    parser.add_argument(
        "--interests", default=None, help="Path to interests.yaml (default: repo root)."
    )
    parser.add_argument(
        "--top", type=int, default=15, help="How many top articles to show (default 15)."
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the embedding cache and re-embed everything.",
    )
    args = parser.parse_args()

    _configure_logging()
    init_tracing()

    profile = load_interests_profile(args.interests)
    raw, _ = _load_and_ingest(args)
    articles = dedup_articles(raw, similarity_threshold=args.threshold)

    client = EmbeddingClient(
        model=profile.embedding_model,
        cache_path=None if args.no_cache else DEFAULT_CACHE_PATH,
    )
    try:
        scored = score_articles(articles, profile=profile, client=client)
    except MissingAPIKeyError as exc:
        raise SystemExit(f"\n{exc}")

    if not scored:
        raise SystemExit("\nNo articles to score — try a wider --hours window.")

    ranked = sorted(scored, key=lambda a: a.score or 0.0, reverse=True)

    print("\n=== Score summary ===")
    print(f"  scored: {len(ranked)} articles against {len(profile.areas)} areas")
    print(
        f"  embeddings: {client.texts_embedded} new, {client.cache_hits} cached, "
        f"{client.api_calls} API call(s)"
    )

    _print_ranked(ranked[: args.top], f"top {min(args.top, len(ranked))}:")
    _print_ranked(ranked[-5:], "bottom 5 (should look skippable):")

    print("\n  best-matching area — whole batch vs. top N:")
    overall = Counter(a.matched_area for a in ranked)
    top_n = Counter(a.matched_area for a in ranked[: args.top])
    for area in profile.areas:
        print(
            f"    {area.name:<20} all:{overall.get(area.name, 0):>4}   "
            f"top:{top_n.get(area.name, 0):>3}"
        )


if __name__ == "__main__":
    ingest_main()
