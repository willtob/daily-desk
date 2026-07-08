"""Phase 2 — dedup / normalize node.

Two jobs, per the plan:

1. Normalize: strip HTML out of RSS summaries and tidy up titles.
2. Dedup: collapse near-identical stories that show up across sources, using URL
   canonicalization + title-token similarity. No embeddings here — those are
   saved for scoring in Phase 3.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from langsmith import traceable

from esp_news.models import Article

logger = logging.getLogger(__name__)

# Function words dropped before comparing titles, so overlap reflects the actual
# story rather than shared filler.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been", "this",
    "that", "it", "its", "you", "your", "we", "our", "will", "has", "have", "how",
    "why", "what", "who", "into", "out", "up", "over", "after", "than", "not",
}

# Query params dropped during URL canonicalization (tracking / share junk).
_TRACKING_PREFIXES = ("utm_", "ref")
_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "source"}

_MAX_DT = datetime.max.replace(tzinfo=timezone.utc)


def _strip_html(raw: str) -> str:
    """Turn an HTML RSS summary into clean plain text."""
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(separator=" ")).strip()


def _clean_title(title: str) -> str:
    """Unescape entities and collapse whitespace in a title."""
    return re.sub(r"\s+", " ", html.unescape(title or "")).strip()


def _canonical_url(url: str) -> str:
    """Normalize a URL so trivially-different links compare equal: scheme, a
    leading ``www.``, trailing slash, fragment, and tracking params are dropped."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    kept_q = [
        (k, v)
        for k, v in parse_qsl(parsed.query)
        if not k.lower().startswith(_TRACKING_PREFIXES)
        and k.lower() not in _TRACKING_KEYS
    ]
    query = urlencode(sorted(kept_q))
    return urlunparse(("", netloc, path, "", query, ""))


def _title_tokens(title: str) -> frozenset[str]:
    """Significant lowercase tokens of a title (punctuation, short words, and
    stopwords removed)."""
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    return frozenset(w for w in cleaned.split() if len(w) > 2 and w not in _STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@traceable(run_type="chain", name="dedup")
def dedup_articles(
    articles: list[Article], *, similarity_threshold: float = 0.6
) -> list[Article]:
    """Normalize article text, then drop duplicate / near-duplicate stories.

    Articles are processed earliest-published first, so the original report of a
    story is the one kept and later duplicates collapse into it.
    """
    # 1. Normalize (strip HTML summaries, clean titles) without mutating inputs.
    normalized = [
        a.model_copy(
            update={"title": _clean_title(a.title), "summary": _strip_html(a.summary)}
        )
        for a in articles
    ]
    normalized.sort(key=lambda a: a.published or _MAX_DT)

    # 2. Collapse exact duplicates by canonical URL.
    by_url: dict[str, Article] = {}
    url_deduped: list[Article] = []
    url_dupes = 0
    for art in normalized:
        key = _canonical_url(art.url)
        if key and key in by_url:
            url_dupes += 1
            existing = by_url[key]
            logger.info(
                "  merged (same url): [%s] %r <- [%s] %r",
                existing.source, existing.title[:48], art.source, art.title[:48],
            )
            continue
        if key:
            by_url[key] = art
        url_deduped.append(art)

    # 3. Collapse near-duplicate stories by title-token similarity.
    kept: list[Article] = []
    kept_tokens: list[frozenset[str]] = []
    title_dupes = 0
    for art in url_deduped:
        tokens = _title_tokens(art.title)
        best_idx: int | None = None
        best_sim = 0.0
        for i, prev in enumerate(kept_tokens):
            sim = _jaccard(tokens, prev)
            if sim >= similarity_threshold and sim > best_sim:
                best_idx, best_sim = i, sim
        if best_idx is None:
            kept.append(art)
            kept_tokens.append(tokens)
        else:
            title_dupes += 1
            orig = kept[best_idx]
            logger.info(
                "  merged (title ~%.2f): [%s] %r <- [%s] %r",
                best_sim, orig.source, orig.title[:48], art.source, art.title[:48],
            )

    logger.info(
        "Dedup: %d -> %d (%d url dupes, %d title dupes)",
        len(articles), len(kept), url_dupes, title_dupes,
    )
    return kept
