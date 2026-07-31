# ESP News Reporter

Personalized tech news digest. A LangGraph pipeline that pulls articles from RSS
feeds, scores them against a personal interest profile using embedding similarity
(the "fitness function"), and renders a curated markdown digest.

v1 is CLI-only and runs on-demand. See [esp-news-plan.md](esp-news-plan.md) for the
full build plan.

## Status
- [x] Phase 0 — repo & environment setup
- [x] Phase 1 — ingest node (fetch + filter RSS)
- [x] Phase 2 — dedup / normalize
- [x] Phase 3 — score (fitness function)
- [x] Phase 4 — curate
- [x] Phase 5 — digest output (v1 complete)
- [x] Phase 6 — FastAPI endpoint serving the digest to the ESP32

## Setup
Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then add your keys
```

## Feeds
37 feeds are configured in [feeds.yaml](feeds.yaml), grouped into 9 themes:
`embedded_wearables`, `big_tech`, `startup_vc`, `ai`, `ai_research`, `ml_applied`,
`eng_blogs`, `florida`, `spain`. Edit freely.

The theme is only a provenance tag — scoring happens against the interest areas in
[interests.yaml](interests.yaml), and any feed can win on any area.

## Running Phase 1 (ingest)
```bash
uv run esp-ingest              # uses the lookback window from feeds.yaml
uv run esp-ingest --hours 72   # override the lookback window
```
Prints article counts per source and per theme.

## Running Phase 2 (dedup)
```bash
uv run esp-dedup                  # ingest, then normalize + collapse duplicates
uv run esp-dedup --threshold 0.5  # looser title-similarity merging
```

## Running Phase 3 (score)
Needs `OPENAI_API_KEY` in `.env` (embeddings: `text-embedding-3-small`).

```bash
uv run esp-score                # ingest -> dedup -> score, prints top 15 + bottom 5
uv run esp-score --top 25
uv run esp-score --no-cache     # re-embed everything instead of reusing .cache/
```

The interest profile lives in [interests.yaml](interests.yaml) — that file is the
fitness function, and it's meant to be edited. Each area has prose (`description`),
concrete headline-ish phrases (`references`), and a `weight`. An article's score is
its single best match across all areas, so it only has to be strong on one thing.

Embeddings are cached in `.cache/embeddings.json` keyed by model + text, so
re-running while tuning the profile is nearly free — only new text hits the API.

## Running Phases 4–5 (curate + digest)
```bash
uv run esp-curate               # rank + cap + suppress repeats, print the front page
uv run esp-digest               # run the whole graph, print and write digests/<date>.md
uv run esp-digest --dry-run     # print only — writes nothing, marks nothing as seen
uv run esp-digest --top 15 --per-area-cap 4
uv run esp-digest --no-seen     # allow articles from earlier digests to reappear
```

`esp-digest` is the v1 entry point: it runs the full LangGraph pipeline
(`ingest → dedup → score → curate → digest`), prints the markdown, writes it to
`digests/YYYY-MM-DD.md`, and records what it showed.

**Per-area cap.** A pure top-N by score would hand back a page of Barcelona
city news most days — those papers simply publish more than the tech blogs. The
cap (default 3) limits any one interest area; if that leaves the page short, a
second pass backfills by score, so the cap shapes the page without shrinking it.

**Cross-run suppression.** `digests/seen.json` remembers which articles have
already appeared, keyed by canonical URL so a link that picks up tracking params
still counts. Entries expire after 45 days. Only articles that actually made a
digest are recorded, and only after the file is written — a crash mid-run can't
silently suppress them from the next one. Use `--no-seen` to ignore it.

Each entry carries a why-it-scored line — winning area, score, and runner-up.
That's what makes the digest log useful for tuning `interests.yaml`: when a
story looks wrong, the line shows whether it won on the area you'd expect.

## Running Phase 6 (serve to the ESP32)
```bash
uv run esp-serve                  # binds 0.0.0.0:8000 so the board can reach it
uv run esp-serve --port 8010      # port 8000 is taken by Docker on this machine
```

| Endpoint | Purpose |
|---|---|
| `GET /digest.json` | the firmware contract; `?limit=` and `?max_summary=` |
| `GET /digest.md` | today's markdown (falls back to the most recent) |
| `GET /health` | freshness, age in hours, refresh status, last error |
| `POST /refresh` | runs the pipeline in the background, returns `202` immediately |

The server reads the last written digest instead of running the pipeline per
request. A full run takes ~20–30 s and the firmware's HTTP client times out at
8 s, so an on-request pipeline would guarantee the device never got a response.
`/digest.json` answers in under 10 ms.

`latest.json` is written via a temp file and atomic rename, so a poll landing
mid-write can't read a truncated payload.

Defaults (`limit=12`, `max_summary=400`) match the firmware's fixed buffers in
`news_client.h`, so the device never parses payload it would only discard.

### Pointing the firmware at it
In `NewsDisplay/src/news_client.cpp`:

```c
#define NEWS_URL "http://192.168.1.171:8000/digest.json"
```

That's this Mac's current LAN address — it can change on DHCP renewal, so a
reserved address or an `.local` hostname is worth setting up if the display is
meant to keep working unattended.

## The device (Phases 7–8)
The ESP32 firmware lives in [firmware/](firmware) — same repo, since it's the
same project. It fetches `/digest.json`, shows the curated stories on a
172×640 touch panel, and reads them aloud via `/audio/{i}.pcm`.

Start there with [firmware/CLAUDE.md](firmware/CLAUDE.md); board notes are in
[docs/HARDWARE.md](docs/HARDWARE.md).

**For UI work, use the desktop simulator** (`firmware/sim/`) rather than
flashing — it builds the real UI source on the host and can be screenshotted,
which turns a ~23 s flash-and-squint cycle into ~2 s.

LVGL is not vendored here; it stays with the Waveshare checkout on the Desktop
and is referenced by absolute path from `firmware/platformio.ini` and
`firmware/sim/Makefile`.

## Tracing (LangSmith)
Set in `.env`:
```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=esp-news-reporter
```
When unset, tracing is a no-op and the pipeline runs identically.
