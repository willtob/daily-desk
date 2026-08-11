"""Phase 9 — summarize node.

Replaces the RSS blurb with something worth reading. What the feeds hand over
is frequently not a summary at all: Hacker News ships link metadata, publisher
feeds ship a teaser cut mid-sentence, and a few ship nothing. This node fetches
the real article and has an LLM write the summary the display shows.

**It runs after curate, not before.** Curate takes ~500 scored articles down to
10, so summarizing earlier would mean fetching and summarizing 500 pages to
throw 490 away — fifty times the cost and minutes of latency for output nobody
reads. Scoring keeps using the cheap RSS text; it only needs enough signal to
rank, which it demonstrably has.

Two rules keep it honest:

* **No text, no summary.** If the fetch fails — paywall, JS-only page, 403 —
  the RSS summary stands. Asking a model to expand ``Points: 58 # Comments: 32``
  into a paragraph doesn't recover the article, it invents one.
* **Some sources are exempt.** A weather alert's RSS body already *is* the
  alert, verbatim and authoritative. Paraphrasing it can only lose the county
  name.
"""

from __future__ import annotations

import logging

from langsmith import traceable

from esp_news.extract import MIN_USABLE_CHARS, ArticleFetcher
from esp_news.models import Article
from esp_news.summarize import Summarizer

logger = logging.getLogger(__name__)

# Sources whose feed text is already the primary document and shouldn't be
# reworded. Both publish machine-generated alerts where the exact phrasing
# ("Until 1100 PM EDT", "Central Suwannee County") is the information.
NO_LLM_SOURCES = frozenset({"NWS Florida Alerts", "NHC Atlantic"})


@traceable(run_type="chain", name="summarize")
def summarize_articles(
    curated: list[Article],
    *,
    fetcher: ArticleFetcher | None = None,
    summarizer: Summarizer | None = None,
) -> list[Article]:
    """Attach an LLM-written summary to each curated article.

    Returns copies with ``long_summary``, ``content`` and ``summary_source``
    filled in; the original RSS ``summary`` is left untouched so the two can be
    compared later. Articles that couldn't be fetched come back unchanged apart
    from ``summary_source``.
    """
    if not curated:
        return []

    fetcher = fetcher or ArticleFetcher()
    summarizer = summarizer or Summarizer()

    # 1. Work out which articles are even candidates.
    candidates = [a for a in curated if a.source not in NO_LLM_SOURCES]
    exempt = len(curated) - len(candidates)
    if exempt:
        logger.info("Summarize: %d articles exempt (%s)", exempt, ", ".join(NO_LLM_SOURCES))

    # 2. Fetch their pages concurrently.
    pages = fetcher.fetch_many([a.url for a in candidates])

    # 3. Summarize the ones that came back with real text — plus the ones whose
    #    feed already ships a full body. Reddit is the case that matters: its
    #    pages are JS-rendered so extraction gets nothing, but the selftext is
    #    right there in the RSS. The MIN_USABLE_CHARS floor is what keeps this
    #    from quietly re-admitting teasers, which is the thing being fixed.
    jobs: list[tuple[str, str, str]] = []
    job_urls: list[str] = []
    for art in candidates:
        result = pages.get(art.url)
        text = result.text if result is not None else ""
        if not text and len(art.summary.strip()) >= MIN_USABLE_CHARS:
            text = art.summary.strip()
        if text:
            jobs.append((art.title, art.source, text))
            job_urls.append(art.url)

    summaries = summarizer.summarize_many(jobs)
    by_url = dict(zip(job_urls, summaries))

    # 4. Rebuild the list, falling back to the RSS summary wherever anything
    #    upstream came up empty.
    out: list[Article] = []
    for art in curated:
        if art.source in NO_LLM_SOURCES:
            out.append(art.model_copy(update={"summary_source": "exempt"}))
            continue

        result = pages.get(art.url)
        summary = by_url.get(art.url, "")
        if summary:
            fetched = result is not None and bool(result.text)
            out.append(
                art.model_copy(
                    update={
                        "long_summary": summary,
                        "content": result.text if fetched else None,
                        # Distinguished so a source that silently stopped being
                        # fetchable is visible in the digest log.
                        "summary_source": "llm" if fetched else "llm:rss-body",
                    }
                )
            )
        else:
            reason = result.reason if result is not None else "no-result"
            out.append(
                art.model_copy(update={"summary_source": f"rss:{reason or 'llm-failed'}"})
            )

    written = sum(1 for a in out if a.summary_source.startswith("llm"))
    logger.info(
        "Summarize: %d of %d articles got an LLM summary (%d exempt, %d fell back)",
        written,
        len(curated),
        exempt,
        len(curated) - written - exempt,
    )
    return out
