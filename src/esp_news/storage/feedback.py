"""Like/dislike verdicts, and the article text they are recorded against.

The written profile in ``interests.yaml`` says what I *think* I want. This is
the other half: what I actually picked when a digest was in front of me. Both
signals stay — the written one handles cold start, new areas, and anything I
have never been shown.

**Why each record carries the article's embedded text.** The obvious record is
a URL and a verdict. That record rots. News URLs 404, get paywalled, or are
re-pointed at a section index within a year or two, and at that point a verdict
is a vote for a thing nobody can reconstruct. Storing the exact text that was
embedded makes every record self-sufficient: the vector can be recomputed from
it at any time, by any model, with no network.

The second effect is a real but smaller one, and it is worth being precise about
because it is easy to overstate. ``EmbeddingClient`` evicts on ``last_used`` and
refreshes that timestamp on every lookup, so re-embedding these texts each run
keeps their vectors in the cache indefinitely at no API cost. That holds because
the score node embeds them in its *first* call, before the eviction sweep that
runs at the end of that call — a text embedded only in a later call would be
swept first and re-bought. It also assumes the pipeline runs at least once a
week (``EVICT_AFTER_DAYS``); it runs daily from launchd. But the cache is an
optimisation and ``.cache/`` is disposable and gitignored, so none of this is
what makes the design sound. The stored text is.

**Format: JSON Lines, append-only.** One verdict per line, newest wins on read.
Three reasons, in order of how much they mattered:

  * *Crash safety without a rewrite.* ``seen.json`` is a single object written
    whole through a temp file and renamed, which is safe but rewrites everything
    to change one entry. Here a verdict is one ``write()`` of one line opened
    ``O_APPEND``: an interrupted run can only damage the final line, and the
    reader skips lines it cannot parse. No existing record can be lost by a
    write that fails.
  * *The history is the data.* Changing your mind about an article is a fact
    worth keeping — this is a labelled dataset in the making, and a store that
    overwrites in place throws away the sequence. Replaying the log to get the
    current state costs nothing at this size.
  * *Human-readable.* One line per verdict, greppable, and a sane diff when it
    is committed. It lives at the repo root next to ``interests.yaml``, tracked
    rather than ignored, because losing it means starting the labelling over —
    unlike the digests and caches, which regenerate.

Clearing a verdict appends a tombstone rather than editing the file, so undo is
just another line and the log stays append-only.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from esp_news.models import Article
from esp_news.nodes.dedup import _canonical_url

logger = logging.getLogger(__name__)

# Next to interests.yaml: src/esp_news/storage/feedback.py -> parents[3].
DEFAULT_FEEDBACK_PATH = Path(__file__).resolve().parents[3] / "feedback.jsonl"

# The lookup side-car lives in .cache/ because it is disposable: losing it means
# a verdict on an older digest falls back to the displayed summary, and the next
# pipeline run repopulates it.
DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[3] / ".cache" / "article-index.json"

LIKE = "like"
DISLIKE = "dislike"
CLEAR = "clear"
VERDICTS = (LIKE, DISLIKE, CLEAR)

# How long an article stays reachable for a verdict after it was last in a
# digest. Matches the seen store's retention: the same 45-day window in which an
# article is still "recent" as far as the rest of the pipeline is concerned.
DEFAULT_INDEX_RETENTION_DAYS = 45


class FeedbackRecord(BaseModel):
    """One verdict on one article, at one moment."""

    verdict: str
    url: str
    title: str = ""
    # The exact string that was embedded for this article. See the module
    # docstring — this is the field that makes the record outlive the URL.
    text: str = ""
    matched_area: str | None = None
    score: float | None = None
    # Where `text` came from: "embedded" is the string the scorer actually used,
    # "display" is a reconstruction from what the client was shown, which is what
    # you get for a verdict on a digest older than the index. Both are usable
    # vectors; only the first one shares a cache key with the scoring run, so
    # this is the difference between a record that is free to keep warm and one
    # that costs an embed. Worth knowing rather than guessing later.
    text_source: str = "embedded"
    recorded: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def key(self) -> str:
        """Canonical URL — the same normalization dedup and the seen store use."""
        return _canonical_url(self.url) or self.url


class FeedbackStore:
    """The append-only verdict log, replayed on read."""

    def __init__(self, path: str | Path | None = None) -> None:
        # Resolved here rather than as a default argument so the module constant
        # can be pointed somewhere else — which is how the tests keep their
        # verdicts out of the real log, and the real log out of the tests.
        self.path = Path(path) if path else DEFAULT_FEEDBACK_PATH

    # ── writing ──────────────────────────────────────────────────────────────

    def _append(self, record: FeedbackRecord) -> FeedbackRecord:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json() + "\n"
        # One write of one line, then fsync. The append itself is what keeps the
        # rest of the file safe; the fsync is so a verdict survives losing power
        # between the tap and the next run, which is the whole point of writing
        # it down immediately rather than at the end of a session.
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def record(
        self,
        verdict: str,
        *,
        url: str,
        title: str = "",
        text: str = "",
        matched_area: str | None = None,
        score: float | None = None,
        text_source: str = "embedded",
    ) -> FeedbackRecord:
        """Append a verdict. Re-recording the same URL supersedes the old one.

        Idempotent by replay rather than by search: the newest line for a URL is
        the one that counts, so recording ``like`` twice leaves two lines and one
        verdict. That is the intended trade — the log keeps the history, the
        reader keeps the state.
        """
        if verdict not in VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r} (expected one of {VERDICTS})")
        return self._append(
            FeedbackRecord(
                verdict=verdict,
                url=url,
                title=title,
                text=text,
                matched_area=matched_area,
                score=score,
                text_source=text_source,
            )
        )

    def clear(self, url: str) -> bool:
        """Append a tombstone. Returns whether there was a verdict to remove.

        A mis-swipe has to be undoable, so this is not a special case — it is the
        same append as any other verdict, and clearing something that was never
        rated is a no-op rather than an error.
        """
        if self.get(url) is None:
            return False
        self.record(CLEAR, url=url)
        return True

    # ── reading ──────────────────────────────────────────────────────────────

    def _replay(self) -> dict[str, FeedbackRecord]:
        """Current state: the last line for each URL, tombstones removed."""
        if not self.path.exists():
            return {}

        latest: dict[str, FeedbackRecord] = {}
        skipped = 0
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Ignoring unreadable feedback log %s: %s", self.path, exc)
            return {}

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = FeedbackRecord(**json.loads(line))
            except (json.JSONDecodeError, ValueError):
                # A torn final line from an interrupted append, or something
                # hand-edited badly. One bad line must not cost the other 300.
                skipped += 1
                continue
            latest[record.key] = record

        if skipped:
            logger.warning("Skipped %d unparseable line(s) in %s", skipped, self.path)
        return {k: r for k, r in latest.items() if r.verdict != CLEAR}

    def get(self, url: str) -> FeedbackRecord | None:
        """The current verdict for a URL, or None."""
        return self._replay().get(_canonical_url(url) or url)

    def verdicts(self) -> dict[str, FeedbackRecord]:
        """Every current verdict, keyed by canonical URL."""
        return self._replay()

    def __len__(self) -> int:
        return len(self._replay())

    def texts_by_area(self, verdict: str) -> dict[str, list[str]]:
        """Embedded texts for one verdict, grouped by the article's matched area.

        Grouping by ``matched_area`` is what keeps a mislabelled verdict cheap.
        A like recorded against ``florida`` can only ever move the ``florida``
        column, exactly as a written ``avoid:`` phrase can only move its own —
        so the blast radius of getting one wrong is one area rather than the
        whole profile. Records with no area, or with no text to embed, are
        dropped here: there is nothing they could usefully be attached to.
        """
        grouped: dict[str, list[str]] = {}
        for record in self._replay().values():
            if record.verdict != verdict or not record.matched_area:
                continue
            text = record.text.strip()
            if text:
                grouped.setdefault(record.matched_area, []).append(text)
        return grouped

    def summary(self) -> str:
        """One line for the logs: how much feedback is in play this run."""
        current = self._replay().values()
        likes = sum(1 for r in current if r.verdict == LIKE)
        dislikes = sum(1 for r in current if r.verdict == DISLIKE)
        return f"{likes} like(s), {dislikes} dislike(s)"


class ArticleIndex:
    """Canonical URL -> what an article was, for articles that reached a digest.

    A client can only name an article by URL, but a verdict has to be stored
    against the text that was embedded — and that text is *not* recoverable from
    what the client was shown. ``digest.json`` carries the LLM summary; the
    scorer embedded the title plus the raw RSS blurb. This is the only place
    those two are joined up.

    Merged across runs rather than replaced, so a verdict on the digest you were
    reading when the pipeline rebuilt underneath you still resolves. Pruned on
    the same 45-day window as the seen store.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_INDEX_PATH   # see FeedbackStore
        self._entries: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable article index %s: %s", self.path, exc)
            return {}

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, url: str) -> dict | None:
        return self._entries.get(_canonical_url(url) or url)

    def add(self, articles: list[Article], *, when: date | None = None) -> None:
        """Record what each article was, ready for a verdict to be attached."""
        stamp = (when or date.today()).isoformat()
        for article in articles:
            key = _canonical_url(article.url) or article.url
            if not key:
                continue
            self._entries[key] = {
                "url": article.url,
                "title": article.title,
                "text": article.embedding_text,
                "matched_area": article.matched_area,
                "score": article.score,
                "indexed": stamp,
            }

    def prune(self, retention_days: int = DEFAULT_INDEX_RETENTION_DAYS) -> int:
        cutoff = date.today() - timedelta(days=retention_days)
        stale = []
        for key, entry in self._entries.items():
            try:
                if date.fromisoformat(entry.get("indexed", "")) < cutoff:
                    stale.append(key)
            except ValueError:
                stale.append(key)
        for key in stale:
            del self._entries[key]
        return len(stale)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._entries, ensure_ascii=False, indent=0, sort_keys=True))
        tmp.replace(self.path)   # atomic; a reader mid-write can't see half a file
        logger.info("Article index: %d entries (%s)", len(self._entries), self.path)


def index_digest(articles: list[Article], *, path: str | Path | None = None) -> None:
    """Add a finished digest to the index and prune it. Never raises.

    Called at the end of a pipeline run, after the digest is on disk. A failure
    here must not fail the run: the worst case is that a verdict on today's
    digest records the displayed text instead of the embedded text, which is a
    slightly worse record, not a broken one.
    """
    try:
        index = ArticleIndex(path)
        index.add(articles)
        index.prune()
        index.save()
    except (OSError, ValueError) as exc:
        logger.warning("Could not update the article index: %s", exc)
