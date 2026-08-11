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


def _render_article(article: Article) -> list[str]:
    """One article as markdown: heading, why-it-scored line, summary."""
    title = article.title or "(untitled)"
    lines = [
        f"### [{title}]({article.url})" if article.url else f"### {title}",
    ]

    why = f"{article.source} · **{article.score:.3f}**"
    # What the like/dislike verdicts did to this article, when they did
    # anything. The whole log exists to make a wrong-looking pick explainable,
    # and "the profile chose this" and "a verdict of mine chose this" are the
    # two answers it has to be able to tell apart.
    if article.base_score is not None:
        shift = (article.score or 0.0) - article.base_score
        if abs(shift) >= 0.0005:
            why += f" · feedback {shift:+.3f}"
    # For the wildcard the winning area is the closest the profile could get,
    # not a match, so name it as such rather than letting it read like a hit.
    if article.is_wildcard:
        why += f" · closest area: {article.matched_area or 'none'}"
    else:
        runner = _runner_up(article)
        if runner:
            why += f" · runner-up: {runner[0]} ({runner[1]:.3f})"
    if article.published:
        why += f" · {article.published.date().isoformat()}"
    # Only worth saying when it *isn't* the LLM summary — that's the case where
    # the page is worth a look.
    if article.summary_source and article.summary_source != "llm":
        why += f" · summary: {article.summary_source}"
    lines += [why, ""]

    summary = trim_text(display_summary(article))
    if summary:
        lines += [summary, ""]
    return lines


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

    # The wildcard is held out of the grouping and the score range: it is not a
    # result of the fitness function, and letting it into either would make the
    # page look like the profile dipped when it didn't.
    wildcards = [a for a in curated if a.is_wildcard]
    curated = [a for a in curated if not a.is_wildcard]

    if not curated and not wildcards:
        lines += [
            "*No articles cleared curation today.*",
            "",
            "Everything scored may already have appeared in an earlier digest, "
            "or the lookback window came up empty. Try `--hours 96`, or "
            "`--no-seen` to allow repeats.",
            "",
        ]
        return "\n".join(lines)

    subtitle = f"*{len(curated)} stories"
    if meta.get("scored_count"):
        subtitle += f" curated from {meta['scored_count']} scored articles"
    if meta.get("feed_count"):
        subtitle += f" across {meta['feed_count']} feeds"
    if meta.get("lookback_hours"):
        subtitle += f" · lookback {meta['lookback_hours']}h"
    if curated:
        scores = [a.score or 0.0 for a in curated]
        subtitle += f" · scores {min(scores):.3f}–{max(scores):.3f}"
    if wildcards:
        subtitle += f" · +{len(wildcards)} wildcard"
    subtitle += "*"
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
            lines += _render_article(art)

    # The exploration slot, last on the page and under its own heading — the
    # score line reads as a failure everywhere else in this digest, so it needs
    # to say out loud that scoring badly is why it's here.
    if wildcards:
        lines += [
            "## wildcard",
            "",
            "*Drawn at random from the middle of the pile on purpose — the "
            "profile can only ever hand back more of what it already knows "
            "about.*",
            "",
        ]
        for art in wildcards:
            lines += _render_article(art)

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

    The wildcard is trimmed last rather than first. It is the tail of the list
    and ``limit`` cuts from the tail, so a full page would otherwise drop the
    one article that was added deliberately — on the device that looks like the
    feature silently not working.
    """
    wildcards = [a for a in curated if a.is_wildcard]
    ranked = [a for a in curated if not a.is_wildcard]
    kept = ranked[: max(0, limit - len(wildcards))] + wildcards[:limit]

    return {
        "generated": (when or date.today()).isoformat(),
        "count": len(kept),
        "articles": [
            {
                "title": a.title,
                "summary": trim_text(display_summary(a), max_summary),
                "source": a.source,
                "matched_area": a.matched_area or "",
                "score": round(a.score or 0.0, 4),
                "url": a.url,
                "wildcard": a.is_wildcard,
            }
            for a in kept
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
