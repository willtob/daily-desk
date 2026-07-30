"""Command-line entry points for running pipeline nodes standalone."""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from esp_news.config import FeedsConfig, load_feeds_config
from esp_news.embeddings import DEFAULT_CACHE_PATH, EmbeddingClient, MissingAPIKeyError
from esp_news.graph import run_digest_graph
from esp_news.interests import load_interests_profile
from esp_news.models import Article
from esp_news.nodes.curate import curate_articles
from esp_news.nodes.dedup import dedup_articles
from esp_news.nodes.digest import digest_payload, write_digest, write_digest_json
from esp_news.nodes.ingest import ingest_articles
from esp_news.nodes.score import score_articles
from esp_news.seen import SeenStore
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


def _add_curate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--top", type=int, default=10, help="How many articles to curate (default 10)."
    )
    parser.add_argument(
        "--per-area-cap",
        type=int,
        default=3,
        help="Max articles per interest area before backfilling (0 = off, default 3).",
    )
    parser.add_argument(
        "--no-seen",
        action="store_true",
        help="Allow articles already shown in earlier digests to reappear.",
    )


def _build_client(profile, no_cache: bool) -> EmbeddingClient:
    return EmbeddingClient(
        model=profile.embedding_model,
        cache_path=None if no_cache else DEFAULT_CACHE_PATH,
    )


def curate_main() -> None:
    """Phase 4 checkpoint: does the top N look like a reasonable front page?"""
    parser = _base_parser("Phase 4 — rank and curate scored articles.")
    _add_threshold_arg(parser)
    _add_curate_args(parser)
    parser.add_argument("--interests", default=None, help="Path to interests.yaml.")
    parser.add_argument("--no-cache", action="store_true", help="Bypass embed cache.")
    args = parser.parse_args()

    _configure_logging()
    init_tracing()

    profile = load_interests_profile(args.interests)
    raw, _ = _load_and_ingest(args)
    deduped = dedup_articles(raw, similarity_threshold=args.threshold)

    try:
        scored = score_articles(
            deduped, profile=profile, client=_build_client(profile, args.no_cache)
        )
    except MissingAPIKeyError as exc:
        raise SystemExit(f"\n{exc}")

    seen = None if args.no_seen else SeenStore()
    curated = curate_articles(
        scored,
        top_n=args.top,
        per_area_cap=args.per_area_cap,
        seen=seen,
    )

    print("\n=== Front page ===")
    if not curated:
        raise SystemExit("  nothing cleared curation — try --no-seen or a wider --hours")
    for rank, art in enumerate(curated, 1):
        print(f"  {rank:>2}. {art.score:.4f} [{art.matched_area:<18}] "
              f"{art.source[:18]:<18} {art.title[:52]}")

    print("\n  areas represented:")
    for area, count in Counter(a.matched_area for a in curated).most_common():
        print(f"    {area:<20} {count}")
    if seen is not None:
        print(f"\n  (seen store holds {len(seen)} urls; not updated by this command)")


def digest_main() -> None:
    """Phase 5 checkpoint: run the whole graph and write a real digest."""
    parser = _base_parser("Phase 5 — run the full pipeline and write a digest.")
    _add_threshold_arg(parser)
    _add_curate_args(parser)
    parser.add_argument("--interests", default=None, help="Path to interests.yaml.")
    parser.add_argument("--no-cache", action="store_true", help="Bypass embed cache.")
    parser.add_argument(
        "--out", default=None, help="Directory for the digest (default: digests/)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest without writing it or recording articles as seen.",
    )
    args = parser.parse_args()

    _configure_logging()
    init_tracing()

    config = load_feeds_config(args.feeds)
    if args.hours is not None:
        config.settings.lookback_hours = args.hours
    profile = load_interests_profile(args.interests)
    seen = None if args.no_seen else SeenStore()

    try:
        state = run_digest_graph(
            config,
            profile,
            client=_build_client(profile, args.no_cache),
            seen=seen,
            similarity_threshold=args.threshold,
            top_n=args.top,
            per_area_cap=args.per_area_cap,
        )
    except MissingAPIKeyError as exc:
        raise SystemExit(f"\n{exc}")

    print()
    print(state.digest_markdown)

    if args.dry_run:
        print("\n(dry run — nothing written, nothing marked as seen)")
        return

    path = write_digest(state.digest_markdown, directory=args.out)
    json_path = write_digest_json(
        digest_payload(state.curated_articles), directory=args.out
    )
    print(f"\nWritten to {path}")
    print(f"          {json_path}  (served by esp-serve)")

    # Only record what actually made the digest, and only after it's on disk —
    # a crash mid-run must not silently suppress articles from the next one.
    if seen is not None:
        for art in state.curated_articles:
            seen.add(art.url)
        seen.prune()
        seen.save()


def serve_main() -> None:
    """Phase 6: serve the digest to the ESP32 over the LAN."""
    parser = argparse.ArgumentParser(description="Phase 6 — serve the digest JSON.")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default 0.0.0.0 so the ESP32 can reach it).",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port (default 8000).")
    parser.add_argument(
        "--reload", action="store_true", help="Auto-reload on code changes (dev)."
    )
    args = parser.parse_args()

    _configure_logging()

    import uvicorn

    print(f"\n  Point the firmware's NEWS_URL at:  http://<this-mac-lan-ip>:{args.port}/digest.json")
    print(f"  Endpoints: /digest.json  /digest.md  /health  POST /refresh\n")
    uvicorn.run("esp_news.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    ingest_main()
