"""Phase 6 — FastAPI endpoint serving the latest digest.

Serves from the last written digest rather than running the pipeline per
request. That's not laziness: a full run takes ~30 s (feed fetch plus
embeddings) and the ESP32's HTTP client times out at 8 s, so an
on-request pipeline would guarantee the device never sees a response.
``POST /refresh`` runs the pipeline in the background instead.

    GET  /digest.json   the ESP32 contract; ?limit= and ?max_summary=
    GET  /digest.md     today's markdown, if it exists
    GET  /health        freshness and whether a refresh is running
    POST /refresh       kick off a pipeline run, returns immediately
    GET  /feedback      every current like/dislike, for rendering state
    POST /feedback      record or clear one verdict
    DELETE /feedback    clear one verdict

The feedback endpoints are documented for client authors in
docs/feedback-api.md, which is the contract — keep the two in step.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from openai import APITimeoutError, OpenAIError
from pydantic import BaseModel

# Load .env at import. Without this only /refresh sees the key (it calls
# init_tracing itself), and /audio fails with a confusing 503 despite the key
# being present in the file.
load_dotenv()

from esp_news.config import load_feeds_config
from esp_news.embeddings import MissingAPIKeyError
from esp_news.feedback import (
    CLEAR,
    DISLIKE,
    LIKE,
    VERDICTS,
    ArticleIndex,
    FeedbackStore,
    index_digest,
)
from esp_news.interests import load_interests_profile
from esp_news.models import Article
from esp_news.nodes.dedup import _canonical_url
from esp_news.nodes.digest import (
    DEFAULT_DIGEST_DIR,
    DEFAULT_JSON_LIMIT,
    DEFAULT_JSON_SUMMARY_CHARS,
    digest_payload,
    trim_text,
    write_digest,
    write_digest_json,
)
from esp_news.seen import SeenStore
from esp_news.tracing import init_tracing
from esp_news.tts import (
    PCM_BITS,
    PCM_CHANNELS,
    PCM_SAMPLE_RATE,
    TTSClient,
    duration_seconds,
    speech_text,
)

logger = logging.getLogger(__name__)

LATEST_JSON = DEFAULT_DIGEST_DIR / "latest.json"

app = FastAPI(
    title="esp-news-reporter",
    description="Serves the curated news digest to the ESP32 display.",
    version="0.1.0",
)

# One refresh at a time. A second request while one is running is told so
# rather than queued — the pipeline is not cheap and the caller can retry.
_refresh_lock = threading.Lock()
_refresh_state: dict[str, object] = {"running": False, "last_error": None}

# Shared so the on-disk audio cache is reused across requests.
_tts = TTSClient()


def _load_latest() -> dict:
    if not LATEST_JSON.exists():
        raise HTTPException(
            status_code=503,
            detail="No digest yet — run `uv run esp-digest` or POST /refresh.",
        )
    try:
        return json.loads(LATEST_JSON.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Unreadable digest: {exc}")


def _run_pipeline() -> None:
    """Run the full graph and rewrite both digest artefacts.

    Imported lazily so that merely starting the server doesn't require an
    OpenAI key — only refreshing does.
    """
    from esp_news.graph import run_digest_graph

    if not _refresh_lock.acquire(blocking=False):
        logger.info("Refresh already in progress; skipping")
        return
    try:
        _refresh_state["running"] = True
        _refresh_state["last_error"] = None

        init_tracing()
        config = load_feeds_config()
        profile = load_interests_profile()
        seen = SeenStore()

        state = run_digest_graph(
            config, profile, seen=seen, feedback=FeedbackStore()
        )

        write_digest(state.digest_markdown or "")
        write_digest_json(digest_payload(state.curated_articles))
        # Same as the CLI: record what each article was, so a verdict arriving
        # later can be stored against the text that was embedded.
        index_digest(state.curated_articles)

        for art in state.curated_articles:
            seen.add(art.url)
        seen.prune()
        seen.save()
        logger.info("Refresh complete: %d articles", len(state.curated_articles))
    except Exception as exc:  # noqa: BLE001 — surfaced via /health, must not kill the server
        logger.exception("Refresh failed")
        _refresh_state["last_error"] = str(exc)
    finally:
        _refresh_state["running"] = False
        _refresh_lock.release()


@app.get("/digest.json")
def get_digest_json(
    limit: int = Query(DEFAULT_JSON_LIMIT, ge=1, le=50),
    max_summary: int = Query(DEFAULT_JSON_SUMMARY_CHARS, ge=0, le=4000),
) -> JSONResponse:
    """The digest in the shape the firmware expects.

    Defaults match the device's buffers; the query params exist so a richer
    client (or a debugging browser tab) can ask for more.
    """
    payload = _load_latest()
    stored = payload.get("articles", [])
    # Trim from the ranked stories, never the wildcard — same reasoning as
    # digest_payload: it lives at the tail, and a plain slice would drop the one
    # article that was put there on purpose.
    wildcards = [a for a in stored if a.get("wildcard")]
    ranked = [a for a in stored if not a.get("wildcard")]
    articles = ranked[: max(0, limit - len(wildcards))] + wildcards[:limit]

    if max_summary < DEFAULT_JSON_SUMMARY_CHARS:
        for art in articles:
            art["summary"] = trim_text(art.get("summary", ""), max_summary)

    return JSONResponse(
        {
            "generated": payload.get("generated", ""),
            "count": len(articles),
            "articles": articles,
        }
    )


@app.get("/digest.md", response_class=PlainTextResponse)
def get_digest_markdown() -> str:
    """Today's markdown digest, or the most recent one on disk."""
    today = DEFAULT_DIGEST_DIR / f"{date.today().isoformat()}.md"
    if today.exists():
        return today.read_text()

    dated = sorted(DEFAULT_DIGEST_DIR.glob("20*.md"), reverse=True)
    if not dated:
        raise HTTPException(status_code=503, detail="No digest written yet.")
    return dated[0].read_text()


@app.get("/health")
def health() -> dict:
    """Freshness of the served digest, and refresh status."""
    exists = LATEST_JSON.exists()
    generated = None
    age_hours = None

    if exists:
        try:
            generated = json.loads(LATEST_JSON.read_text()).get("generated")
        except (OSError, json.JSONDecodeError):
            generated = None
        mtime = datetime.fromtimestamp(LATEST_JSON.stat().st_mtime, tz=timezone.utc)
        age_hours = round(
            (datetime.now(timezone.utc) - mtime).total_seconds() / 3600, 2
        )

    return {
        "status": "ok" if exists else "no-digest",
        "generated": generated,
        "age_hours": age_hours,
        "refreshing": _refresh_state["running"],
        "last_error": _refresh_state["last_error"],
    }


@app.get("/audio/{index}.pcm")
def get_audio(index: int) -> Response:
    """Raw PCM narration of article ``index`` (0-based) from the current digest.

    16 kHz, 16-bit signed little-endian, mono — the firmware writes these bytes
    to I2S directly, so no decoder is needed on the device. Synthesis is cached
    by content, so replaying an article costs nothing.

    Content-Length is always set: the firmware needs to know how much to expect
    rather than reading until the socket closes.
    """
    payload = _load_latest()
    articles = payload.get("articles", [])
    if index < 0 or index >= len(articles):
        raise HTTPException(
            status_code=404,
            detail=f"Article {index} out of range (digest has {len(articles)}).",
        )

    text = speech_text(articles[index])
    if not text.strip():
        raise HTTPException(status_code=404, detail="Article has nothing to read.")

    try:
        audio = _tts.synthesize(text)
    except MissingAPIKeyError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except APITimeoutError:
        # Synthesis stalled upstream. 504 rather than 500 so it reads as
        # "try again", which is exactly right — the device's next tap usually
        # succeeds, and a completed synthesis lands in the cache either way.
        raise HTTPException(status_code=504, detail="Speech synthesis timed out.")
    except OpenAIError as exc:
        logger.warning("Synthesis failed for article %d: %s", index, exc)
        raise HTTPException(status_code=502, detail=f"Speech synthesis failed: {exc}")

    return Response(
        content=audio,
        media_type="audio/L16",
        headers={
            "Content-Length": str(len(audio)),
            "X-Sample-Rate": str(PCM_SAMPLE_RATE),
            "X-Bits-Per-Sample": str(PCM_BITS),
            "X-Channels": str(PCM_CHANNELS),
            "X-Duration-Seconds": f"{duration_seconds(len(audio)):.1f}",
        },
    )


@app.post("/refresh", status_code=202)
def refresh() -> dict:
    """Kick off a pipeline run in the background and return immediately."""
    if _refresh_state["running"]:
        return {"status": "already-running"}
    threading.Thread(target=_run_pipeline, name="refresh", daemon=True).start()
    return {"status": "started"}


# ── Feedback ─────────────────────────────────────────────────────────────────
#
# The client knows a URL and nothing else. Everything a verdict has to record —
# the title, the matched area, the score, and above all the exact text that was
# embedded — is resolved here from the server's own records, so a client cannot
# get it wrong and does not need to be updated when the record grows a field.
#
# Resolution order is the article index first (the embedded text, written by
# every pipeline run) and the served digest second (a reconstruction from what
# was displayed, for a verdict on a digest older than the index). Only if both
# miss is this a 404 — a verdict on an article the server has never heard of is
# the one case where guessing would be worse than failing.


class FeedbackRequest(BaseModel):
    """One verdict. ``clear`` removes whatever verdict the URL currently has."""

    url: str
    verdict: str


def _resolve_article(url: str) -> tuple[dict, str] | None:
    """What this URL was, and where that came from."""
    entry = ArticleIndex().get(url)
    if entry:
        return entry, "embedded"

    try:
        served = _load_latest().get("articles", [])
    except HTTPException:
        # No digest on disk yet. That is a reason this URL is unknown, not a
        # separate failure to report — the caller gets the same 404 either way.
        return None

    key = _canonical_url(url) or url
    for article in served:
        if (_canonical_url(article.get("url", "")) or "") == key:
            # Rebuilt through the model so the truncation matches the scorer's,
            # even though the summary underneath it does not.
            stand_in = Article(
                title=article.get("title", ""),
                url=article.get("url", url),
                source=article.get("source", ""),
                theme="",
                summary=article.get("summary", ""),
            )
            return {
                "url": article.get("url", url),
                "title": stand_in.title,
                "text": stand_in.embedding_text,
                "matched_area": article.get("matched_area") or None,
                "score": article.get("score"),
            }, "display"
    return None


@app.get("/feedback")
def get_feedback(
    url: str | None = Query(None, description="Return only this article's verdict."),
    include_text: bool = Query(
        False, description="Include the stored embedded text (large)."
    ),
) -> JSONResponse:
    """Every current verdict, or one of them.

    ``?url=`` returns ``{"url": ..., "verdict": null}`` rather than a 404 when
    nothing is recorded: "no verdict" is a normal state for a card to render,
    not an error.

    The stored text is omitted unless asked for. It runs to a thousand
    characters an article and a client rendering thumbs does not need it.
    """
    store = FeedbackStore()
    current = store.verdicts()

    def shape(record) -> dict:
        data = record.model_dump(mode="json")
        if not include_text:
            data.pop("text", None)
        return data

    if url is not None:
        record = current.get(_canonical_url(url) or url)
        return JSONResponse(
            {"url": url, "verdict": record.verdict if record else None,
             "record": shape(record) if record else None}
        )

    records = sorted(current.values(), key=lambda r: r.recorded, reverse=True)
    return JSONResponse(
        {
            "count": len(records),
            "likes": sum(1 for r in records if r.verdict == LIKE),
            "dislikes": sum(1 for r in records if r.verdict == DISLIKE),
            "verdicts": [shape(r) for r in records],
        }
    )


@app.post("/feedback")
def post_feedback(body: FeedbackRequest) -> JSONResponse:
    """Record a verdict, or clear one with ``verdict: "clear"``.

    Idempotent: re-sending the same verdict for the same URL leaves one verdict
    in force. The log keeps both lines, because changing your mind is data.
    """
    if body.verdict not in VERDICTS:
        raise HTTPException(
            status_code=422,
            detail=f"verdict must be one of {list(VERDICTS)}, got {body.verdict!r}",
        )

    store = FeedbackStore()

    if body.verdict == CLEAR:
        existed = store.clear(body.url)
        return JSONResponse(
            {"url": body.url, "verdict": None, "cleared": existed}
        )

    resolved = _resolve_article(body.url)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown article — not in the index or the current digest.",
        )
    entry, text_source = resolved

    record = store.record(
        body.verdict,
        url=entry.get("url", body.url),
        title=entry.get("title", ""),
        text=entry.get("text", ""),
        matched_area=entry.get("matched_area"),
        score=entry.get("score"),
        text_source=text_source,
    )
    logger.info(
        "Feedback: %s [%s] %s", record.verdict, record.matched_area, record.title[:60]
    )
    return JSONResponse({"url": record.url, "verdict": record.verdict,
                         "record": record.model_dump(mode="json")})


@app.delete("/feedback")
def delete_feedback(url: str = Query(..., description="Article URL to clear.")) -> dict:
    """Clear a verdict. The same operation as ``POST`` with ``clear``.

    Both spellings exist because both callers are real: the firmware already
    speaks POST and nothing else, and everything else expects DELETE to be the
    way you undo a thing.
    """
    return {"url": url, "verdict": None, "cleared": FeedbackStore().clear(url)}
