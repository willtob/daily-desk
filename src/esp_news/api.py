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
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from esp_news.config import load_feeds_config
from esp_news.interests import load_interests_profile
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

        state = run_digest_graph(config, profile, seen=seen)

        write_digest(state.digest_markdown or "")
        write_digest_json(digest_payload(state.curated_articles))

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
    articles = payload.get("articles", [])[:limit]

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


@app.post("/refresh", status_code=202)
def refresh() -> dict:
    """Kick off a pipeline run in the background and return immediately."""
    if _refresh_state["running"]:
        return {"status": "already-running"}
    threading.Thread(target=_run_pipeline, name="refresh", daemon=True).start()
    return {"status": "started"}
