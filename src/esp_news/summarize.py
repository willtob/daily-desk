"""Phase 9b — LLM summaries of article text, with an on-disk cache.

Separate from the node (``nodes/summarize.py``) for the same reason
``embeddings.py`` is separate from ``nodes/score.py``: the client is about
talking to OpenAI and not paying twice for the same work, the node is about
what the pipeline does with it.

Runs on the **Responses API** rather than chat completions. The gpt-5 family
are reasoning models, and ``responses.create`` is where ``reasoning`` and
``max_output_tokens`` live; effort is pinned low because summarizing prose that
is already in front of the model doesn't need deliberation, and low effort is
the difference between a two-second call and a fifteen-second one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from esp_news.embeddings import MissingAPIKeyError

logger = logging.getLogger(__name__)

# .cache/summaries.json — src/esp_news/summarize.py -> parents[2].
DEFAULT_SUMMARY_CACHE = Path(__file__).resolve().parents[2] / ".cache" / "summaries.json"

DEFAULT_SUMMARY_MODEL = os.getenv("ESP_NEWS_SUMMARY_MODEL", "gpt-5.4-mini")

# What the device shows. Long enough to say something, short enough to read on
# a 172px-wide panel without it becoming a scrolling chore.
DEFAULT_TARGET_CHARS = 800

# Reasoning models bill reasoning tokens against this ceiling too, so it has to
# clear the visible output by a wide margin or the response comes back empty.
_MAX_OUTPUT_TOKENS = 2000

_MAX_WORKERS = 6

# Bump when the prompt changes — it's part of the cache key, so old summaries
# stop being served the moment the instructions that produced them change.
#
# 2: Phase 9c. Allowed a two-item Markdown subset and blank-line paragraphs,
#    where 1 demanded plain prose. Everything cached under 1 was written
#    without paragraph breaks, so the readers still have to cope with a single
#    unbroken block — they insert their own breaks when the text arrives with
#    none. Do not remove that fallback on the strength of this prompt.
PROMPT_VERSION = "2"

# The subset is deliberately two things wide, and the reasons are in
# esp_news/markdown.py: this text is rendered by SwiftUI, by LVGL — which
# cannot do inline bold at all, one font per label — and by a speech model.
# Anything beyond bold and bullets is either stripped or read aloud as
# punctuation, so asking for it only invites the model to waste characters.
#
# The "5 x 3" instruction is not pedantry. A lone asterisk in arithmetic is
# emphasis to a Markdown parser, and the failure is not a stray character, it
# is the rest of the sentence silently changing weight.
_SYSTEM_PROMPT = """\
You write the summary someone reads on a small handheld news display instead of \
opening the article. Assume they will not open it — your summary is all they get.

Formatting:
- Separate paragraphs with a blank line. Two or three short paragraphs read far \
better on a narrow screen than one block.
- You may use **bold**, for the two or three figures, names or outcomes most worth \
spotting at a glance. Never bold a whole sentence.
- You may use "- " at the start of a line for a bullet, but only when the piece \
genuinely is a list of separate items. Prose is the default.
- No other formatting: no headings, links, images, tables, code spans, italics, \
blockquotes, or emoji.
- Never use a bare asterisk for anything else. Write multiplication as "5 x 3".

Rules:
- Aim for {target} characters across 3 to 5 sentences. Never exceed {target}.
- Open with what happened or what the piece argues. Never open with "This article", \
"The author", "In this post", "The piece discusses", or any similar framing.
- Keep the specifics that make it worth knowing: names, organisations, numbers, \
versions, prices, dates, benchmark figures, and the outcome.
- If the piece is an argument or opinion, say what it actually claims, not that it \
makes a claim.
- Use only what is in the supplied text. If the text does not say it, leave it out. \
Do not speculate about what the article probably covers.
- Write in the same language as the article.
"""


class Summarizer:
    """Writes article summaries with an LLM, cached on disk by content."""

    def __init__(
        self,
        model: str = DEFAULT_SUMMARY_MODEL,
        *,
        target_chars: int = DEFAULT_TARGET_CHARS,
        cache_path: str | Path | None = DEFAULT_SUMMARY_CACHE,
    ) -> None:
        self.model = model
        self.target_chars = target_chars
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache = self._load_cache()
        self._client: OpenAI | None = None
        self.api_calls = 0
        self.cache_hits = 0
        self.failures = 0
        # Whether anything has been added to the cache since it was loaded.
        # The counters can't answer that under concurrency — ``+=`` from six
        # worker threads loses increments — and a lost increment would mean
        # summaries that were paid for never reaching disk.
        self._cache_dirty = False

    # ── cache ────────────────────────────────────────────────────────────────

    def _load_cache(self) -> dict[str, dict]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable summary cache %s: %s", self.cache_path, exc)
            return {}

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._cache, ensure_ascii=False))
        tmp.replace(self.cache_path)  # write-then-rename, as with the embedding cache

    def _key(self, title: str, text: str) -> str:
        """Cache key over everything that changes the output.

        The article text is part of it, so a story that gets rewritten after
        publication is re-summarized rather than served stale.
        """
        material = "\x00".join(
            [self.model, PROMPT_VERSION, str(self.target_chars), title, text]
        )
        return hashlib.sha256(material.encode()).hexdigest()

    # ── summarizing ──────────────────────────────────────────────────────────

    def _openai(self) -> OpenAI:
        if self._client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise MissingAPIKeyError(
                    "OPENAI_API_KEY is not set — add it to .env (see .env.example)."
                )
            self._client = OpenAI()
        return self._client

    def summarize(self, title: str, source: str, text: str) -> str:
        """Summarize one article. Returns "" if the model gave nothing usable.

        Network and API errors are swallowed and reported as an empty result —
        the caller falls back to the RSS summary, and one dead article must not
        take the morning's digest with it.
        """
        text = (text or "").strip()
        if not text:
            return ""

        key = self._key(title, text)
        cached = self._cache.get(key)
        if cached:
            self.cache_hits += 1
            return cached["summary"]

        prompt = (
            f"Source: {source}\nHeadline: {title}\n\n"
            f"Article text:\n{text}\n\n"
            f"Write the summary now."
        )
        try:
            response = self._openai().responses.create(
                model=self.model,
                instructions=_SYSTEM_PROMPT.format(target=self.target_chars),
                input=prompt,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                reasoning={"effort": "low"},
            )
            summary = (response.output_text or "").strip()
        except MissingAPIKeyError:
            raise
        except Exception as exc:  # noqa: BLE001 — fall back rather than fail the run
            self.failures += 1
            logger.warning("  summarize failed for %r: %s", title[:48], exc)
            return ""

        self.api_calls += 1
        if not summary:
            self.failures += 1
            logger.warning("  empty summary returned for %r", title[:48])
            return ""

        self._cache[key] = {
            "summary": summary,
            "title": title,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        self._cache_dirty = True
        return summary

    def summarize_many(self, jobs: list[tuple[str, str, str]]) -> list[str]:
        """Summarize ``(title, source, text)`` triples concurrently.

        Contexts are copied into the workers so LangSmith keeps the node as the
        parent run — one copy per job, taken here, since a ``Context`` can only
        be entered by one thread at a time. The cache is saved once at the end
        rather than per call, so ten summaries cost one file write, not ten.

        The save is gated on the dirty flag, not on ``api_calls``: that counter
        is incremented from every worker without a lock, so it can undercount,
        and a count that lands back on zero would drop the run's summaries.
        The flag only ever goes one way, so no interleaving can clear it.
        """
        if not jobs:
            return []

        contexts = [copy_context() for _ in jobs]
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            summaries = list(
                pool.map(lambda c, j: c.run(self.summarize, *j), contexts, jobs)
            )

        if self._cache_dirty:
            self._save_cache()
        logger.info(
            "Summarized %d articles (%d new, %d cached, %d failed)",
            len(jobs),
            self.api_calls,
            self.cache_hits,
            self.failures,
        )
        return summaries
