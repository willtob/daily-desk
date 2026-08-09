"""Phase 5 — digest / output node.

Renders the curated front page as markdown and writes it to a timestamped
file, so there's a running log to look back on and to judge the fitness
function against later.

Each entry carries a one-line "why it scored" — the winning interest area, the
score, and the runner-up area. That line is the whole point of keeping the
log: when a digest looks wrong, it shows whether the profile matched the thing
you'd expect, or won on something unrelated.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

from langsmith import traceable

from esp_news.models import Article

logger = logging.getLogger(__name__)

# digests/ lives at the repo root: src/esp_news/nodes/digest.py -> parents[3].
DEFAULT_DIGEST_DIR = Path(__file__).resolve().parents[3] / "digests"

# Phase 9 summaries are written to a target length already, so this is a guard
# against a runaway model or an untouched feed blurb, not a normal trim.
_MAX_SUMMARY_CHARS = 1200


def display_summary(article: Article) -> str:
    """The summary a reader should actually see.

    The LLM one when Phase 9 produced it, the RSS one otherwise. Everything
    that renders an article goes through here, so a fetch failure degrades to
    the old behaviour instead of showing a blank card.
    """
    return (article.long_summary or article.summary or "").strip()


def _runner_up(article: Article) -> tuple[str, float] | None:
    """Best-scoring area that *didn't* win, for the why-it-scored line."""
    others = {
        area: score
        for area, score in article.area_scores.items()
        if area != article.matched_area
    }
    if not others:
        return None
    area = max(others, key=lambda a: others[a])
    return area, others[area]


def trim_text(text: str, limit: int = _MAX_SUMMARY_CHARS) -> str:
    """Truncate to at most ``limit`` characters, ellipsis included.

    The ellipsis is budgeted inside the limit, not appended after it —
    otherwise asking for 80 characters hands back 82, which quietly breaks
    any caller sizing a buffer from the number it passed in.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return "..."[:limit]
    # Cut on a word boundary so the ellipsis doesn't land mid-word.
    return text[: limit - 3].rsplit(" ", 1)[0] + "..."


@traceable(run_type="chain", name="digest")
def render_digest(
    curated: list[Article],
    *,
    when: date | None = None,
    meta: dict | None = None,
) -> str:
    """Render curated articles as markdown, grouped by interest area.

    Areas are ordered by their best-scoring article, so the strongest match
    leads the page rather than whatever happens to sort first alphabetically.
    """
    when = when or date.today()
    meta = meta or {}

    lines: list[str] = [f"# News Digest — {when.isoformat()}", ""]

    if not curated:
        lines += [
            "*No articles cleared curation today.*",
            "",
            "Everything scored may already have appeared in an earlier digest, "
            "or the lookback window came up empty. Try `--hours 96`, or "
            "`--no-seen` to allow repeats.",
            "",
        ]
        return "\n".join(lines)

    scores = [a.score or 0.0 for a in curated]
    subtitle = f"*{len(curated)} stories"
    if meta.get("scored_count"):
        subtitle += f" curated from {meta['scored_count']} scored articles"
    if meta.get("feed_count"):
        subtitle += f" across {meta['feed_count']} feeds"
    if meta.get("lookback_hours"):
        subtitle += f" · lookback {meta['lookback_hours']}h"
    subtitle += f" · scores {min(scores):.3f}–{max(scores):.3f}*"
    lines += [subtitle, ""]

    # Group by area, ordering areas by their strongest article.
    by_area: dict[str, list[Article]] = defaultdict(list)
    for art in curated:
        by_area[art.matched_area or "unscored"].append(art)
    area_order = sorted(
        by_area, key=lambda a: max(x.score or 0.0 for x in by_area[a]), reverse=True
    )

    for area in area_order:
        lines += [f"## {area}", ""]
        for art in by_area[area]:
            title = art.title or "(untitled)"
            lines.append(f"### [{title}]({art.url})" if art.url else f"### {title}")

            why = f"{art.source} · **{art.score:.3f}**"
            runner = _runner_up(art)
            if runner:
                why += f" · runner-up: {runner[0]} ({runner[1]:.3f})"
            if art.published:
                why += f" · {art.published.date().isoformat()}"
            # Only worth saying when it *isn't* the LLM summary — that's the
            # case where the page is worth a look.
            if art.summary_source and art.summary_source != "llm":
                why += f" · summary: {art.summary_source}"
            lines += [why, ""]

            summary = trim_text(display_summary(art))
            if summary:
                lines += [summary, ""]

    # Footer: what the profile did *not* surface is as useful for tuning as
    # what it did.
    if meta.get("all_areas"):
        missing = [a for a in meta["all_areas"] if a not in by_area]
        if missing:
            lines += ["---", "", f"*No stories today from: {', '.join(missing)}*", ""]

    return "\n".join(lines)


# Defaults chosen to match the firmware's buffers in news_client.h
# (NEWS_MAX_ARTICLES 12, NEWS_SUMMARY_LEN 960) so the device never has to
# discard payload it already spent memory parsing. Raised from 400 in Phase 9:
# an LLM summary trimmed to 400 chars is a teaser again, which is the whole
# thing this phase set out to stop.
DEFAULT_JSON_LIMIT = 12
DEFAULT_JSON_SUMMARY_CHARS = 900


def digest_payload(
    curated: list[Article],
    *,
    when: date | None = None,
    limit: int = DEFAULT_JSON_LIMIT,
    max_summary: int = DEFAULT_JSON_SUMMARY_CHARS,
) -> dict:
    """Structured form of the digest, for the API and the ESP32.

    Field names match the ``Article`` model so the firmware can read them
    straight through with no renaming layer.
    """
    return {
        "generated": (when or date.today()).isoformat(),
        "count": min(len(curated), limit),
        "articles": [
            {
                "title": a.title,
                "summary": trim_text(display_summary(a), max_summary),
                "source": a.source,
                "matched_area": a.matched_area or "",
                "score": round(a.score or 0.0, 4),
                "url": a.url,
            }
            for a in curated[:limit]
        ],
    }


def write_digest_json(
    payload: dict,
    *,
    directory: str | Path | None = DEFAULT_DIGEST_DIR,
) -> Path:
    """Write the payload to ``digests/latest.json`` for the API to serve.

    Always the same filename, not a dated one: the endpoint wants "the current
    digest", and the dated markdown is what serves as the historical log.
    """
    directory = Path(directory) if directory else DEFAULT_DIGEST_DIR
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / "latest.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    tmp.replace(path)   # atomic, so a poll mid-write can't read a half file
    logger.info("Digest JSON written to %s (%d articles)", path, len(payload["articles"]))
    return path


def write_digest(
    markdown: str,
    *,
    when: date | None = None,
    directory: str | Path | None = DEFAULT_DIGEST_DIR,
) -> Path:
    """Write the digest to ``digests/YYYY-MM-DD.md`` and return the path."""
    when = when or date.today()
    directory = Path(directory) if directory else DEFAULT_DIGEST_DIR
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{when.isoformat()}.md"
    path.write_text(markdown)
    logger.info("Digest written to %s (%d chars)", path, len(markdown))
    return path
