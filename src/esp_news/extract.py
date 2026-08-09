"""Phase 9a — fetch an article's full text.

The summarizer is only as good as what it's given, and what the RSS feed gives
is often nothing. Hacker News ships ``Points: 58 # Comments: 32``; publisher
feeds ship a teaser cut mid-sentence to make you click. Handing either to an
LLM produces a fluent paragraph about nothing, so the text has to come from the
page itself.

Extraction is trafilatura's job — separating article prose from nav, cookie
banners and related-links rails is a solved problem that is miserable to
re-solve with hand-written soup selectors. BeautifulSoup remains as a fallback
for the pages it gives up on.

Fetches are cached to individual files under ``.cache/pages/`` rather than one
JSON blob: article text runs to tens of kilobytes, and per-file writes mean a
run interrupted halfway still keeps what it already fetched.
"""

from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

import httpx
import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# trafilatura warns "discarding data: None" for every page it can't make sense
# of. That's an expected outcome here — the caller handles it — so it doesn't
# belong in the digest run's output.
logging.getLogger("trafilatura").setLevel(logging.ERROR)

# .cache/pages/ — src/esp_news/extract.py -> parents[2].
DEFAULT_PAGE_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "pages"

# Same browser-ish UA as the ingest node, for the same reason: a bare client
# gets 403'd by half the major outlets.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) esp-news-reporter/0.1"
)

_FETCH_TIMEOUT_SECONDS = 20
# Enough to keep ten fetches from serialising, low enough to stay polite.
_MAX_WORKERS = 6

# Below this, whatever came back is a paywall stub, a consent interstitial, or
# a JS shell — not an article. Treated as a failed fetch so the caller falls
# back to the RSS summary instead of summarizing "Please enable JavaScript".
MIN_USABLE_CHARS = 600

# Ceiling on what goes to the model. ~24k characters is roughly 6k tokens, more
# than enough context for a summary, and it caps the cost of the one feed that
# publishes a 30-page transcript.
MAX_TEXT_CHARS = 24000

# Non-HTML endpoints, where a fetch can only fail. NWS/NHC "articles" are
# api.weather.gov JSON documents, and their RSS summary is already the full
# alert text — see NO_LLM_SOURCES in nodes/summarize.py.
_SKIP_DOMAINS = ("api.weather.gov",)


class FetchResult:
    """Outcome of one article fetch: the text, and why there isn't any.

    Deliberately has no ``__bool__``. An empty result is still a result — it
    carries the reason the fetch failed — and making it falsy invites
    ``if result:`` where ``if result is not None:`` was meant, which silently
    throws the diagnosis away.
    """

    __slots__ = ("text", "reason")

    def __init__(self, text: str = "", reason: str = "") -> None:
        self.text = text
        self.reason = reason


def _fallback_extract(html: str) -> str:
    """Last-ditch extraction when trafilatura returns nothing.

    Deliberately crude — take the paragraphs and drop the furniture. It only
    has to beat "no text at all", which is the alternative.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = "\n\n".join(p for p in paragraphs if len(p) > 40)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class ArticleFetcher:
    """Fetches and extracts article text, cached on disk by URL."""

    def __init__(self, cache_dir: str | Path | None = DEFAULT_PAGE_CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.fetched = 0
        self.cache_hits = 0
        self.failures = 0

    def cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self.cache_dir / f"{key}.txt"

    def _read_cache(self, url: str) -> str | None:
        path = self.cache_path(url)
        if path and path.exists():
            try:
                return path.read_text()
            except OSError as exc:
                logger.warning("Unreadable page cache %s: %s", path, exc)
        return None

    def _write_cache(self, url: str, text: str) -> None:
        path = self.cache_path(url)
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".txt.tmp")
        tmp.write_text(text)
        tmp.replace(path)  # atomic, so a concurrent reader can't see half a page

    def fetch(self, url: str) -> FetchResult:
        """Return the article body at ``url``, or an empty result with a reason.

        Never raises: a fetch failure is an expected outcome here, not an error
        worth aborting a digest over.
        """
        if not url:
            return FetchResult(reason="no-url")
        if any(d in url for d in _SKIP_DOMAINS):
            return FetchResult(reason="skipped-domain")

        cached = self._read_cache(url)
        if cached is not None:
            self.cache_hits += 1
            return FetchResult(text=cached)

        try:
            response = httpx.get(
                url,
                timeout=_FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — a dead link must not kill the run
            self.failures += 1
            logger.info("  fetch failed %s: %s", url[:70], type(exc).__name__)
            return FetchResult(reason=f"fetch-error:{type(exc).__name__}")

        if "html" not in response.headers.get("content-type", "").lower():
            self.failures += 1
            return FetchResult(reason="not-html")

        html = response.text
        text = trafilatura.extract(
            html, include_comments=False, include_tables=False, favor_precision=True
        ) or _fallback_extract(html)
        text = (text or "").strip()[:MAX_TEXT_CHARS]

        if len(text) < MIN_USABLE_CHARS:
            self.failures += 1
            logger.info("  too little text (%d chars) from %s", len(text), url[:70])
            return FetchResult(reason=f"thin:{len(text)}")

        self.fetched += 1
        self._write_cache(url, text)
        return FetchResult(text=text)

    def fetch_many(self, urls: list[str]) -> dict[str, FetchResult]:
        """Fetch several URLs concurrently, keyed by URL.

        Each worker runs inside a copy of the calling context so LangSmith's
        current run stays the parent — ``ThreadPoolExecutor`` does not carry
        contextvars across on its own, and without this every fetch would show
        up in the trace as an orphaned root run.

        One copy *per job*, all taken here in the calling thread: a ``Context``
        can only be entered once at a time, so sharing a single one across
        workers raises "context is already entered", and copying inside the
        worker would snapshot the worker's context rather than this one's.
        """
        unique = list(dict.fromkeys(u for u in urls if u))
        if not unique:
            return {}

        contexts = [copy_context() for _ in unique]
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            results = list(
                pool.map(lambda c, u: c.run(self.fetch, u), contexts, unique)
            )

        logger.info(
            "Fetched %d pages (%d new, %d cached, %d failed)",
            len(unique),
            self.fetched,
            self.cache_hits,
            self.failures,
        )
        return dict(zip(unique, results))
