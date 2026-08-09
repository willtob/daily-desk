# ESP News Reporter

Personalized tech news digest. A LangGraph pipeline that pulls articles from RSS
feeds, scores them against a personal interest profile using embedding similarity
(the "fitness function"), and renders a curated markdown digest.

Runs on demand from the CLI, every morning at 08:00 via launchd, or on a button
press from the device — see [When the feeds actually update](#when-the-feeds-actually-update).
[esp-news-plan.md](esp-news-plan.md) has the full build plan.

## Status
- [x] Phase 0 — repo & environment setup
- [x] Phase 1 — ingest node (fetch + filter RSS)
- [x] Phase 2 — dedup / normalize
- [x] Phase 3 — score (fitness function)
- [x] Phase 4 — curate
- [x] Phase 5 — digest output (v1 complete)
- [x] Phase 6 — FastAPI endpoint serving the digest to the ESP32
- [x] Phase 9 — fetch the real article and write a proper summary with an LLM

## Setup
Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then add your keys
```

## Feeds
42 feeds are configured in [feeds.yaml](feeds.yaml), grouped into 10 themes:
`embedded_wearables`, `big_tech`, `startup_vc`, `ai`, `ai_research`, `ml_applied`,
`eng_blogs`, `florida`, `barcelona_dates`, `spain`. Edit freely.

`barcelona_dates` is the what's-on half of Barcelona, separate from `spain`'s
what-happened: Barcelona Secreta, Time Out, La Vanguardia's food section,
Barcelona Cultura and betevé's weekend agenda. All five publish in Spanish or
Catalan, which is why the matching interest area carries reference phrases in
both.

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
uv run esp-digest --no-wildcard # drop the low-scoring exploration article
```

`esp-digest` is the v1 entry point: it runs the full LangGraph pipeline
(`ingest → dedup → score → curate → summarize → digest`), prints the markdown,
writes it to `digests/YYYY-MM-DD.md`, and records what it showed.

**Per-area cap.** A pure top-N by score would hand back a page of Barcelona
city news most days — those papers simply publish more than the tech blogs. The
cap (default 3) limits any one interest area; if that leaves the page short, a
second pass backfills by score, so the cap shapes the page without shrinking it.

**Cross-run suppression.** `digests/seen.json` remembers which articles have
already appeared, keyed by canonical URL so a link that picks up tracking params
still counts. Entries expire after 45 days. Only articles that actually made a
digest are recorded, and only after the file is written — a crash mid-run can't
silently suppress them from the next one. Use `--no-seen` to ignore it.

**The wildcard.** After the ranked page, the digest carries one extra article
picked *because* it scored badly — its own `## wildcard` section at the end of
the markdown, last in the JSON, and a cool-tinted `WILDCARD` card on the device
and the widget. The profile is a fitness function, so left alone it can only
ever return more of what it already matches; this is the one slot it doesn't
control. It's drawn at random from the worst-scoring quarter of everything that
cleared the filters rather than from the very bottom — the absolute floor is the
same kind of thing every day (a two-line sports result, a weather bulletin),
which is off-profile without being a new topic. `--no-wildcard` turns it off.

Each entry carries a why-it-scored line — winning area, score, and runner-up.
That's what makes the digest log useful for tuning `interests.yaml`: when a
story looks wrong, the line shows whether it won on the area you'd expect.

## Running Phase 9 (real summaries)
```bash
uv run esp-summarize --no-seen        # RSS blurb vs. LLM summary, side by side
uv run esp-summarize --target-chars 600
uv run esp-digest --no-llm            # skip it — RSS blurbs, no fetch, no cost
```

What the feeds hand over is often not a summary. Hacker News ships
`Article URL: … Points: 58 # Comments: 32`; publisher feeds ship a teaser cut
mid-sentence to make you click. So this node **fetches the article itself** and
has an LLM write the summary from the real text.

It runs **after curate**, not before. Curate takes ~500 scored articles down to
10; summarizing earlier would mean fetching and summarizing 500 pages to throw
490 away. Scoring keeps using the cheap RSS text — it only needs enough signal
to rank.

Two rules keep it honest:

- **No text, no summary.** If the fetch fails — paywall, 403, JS-only page —
  the RSS summary stands. Expanding `Points: 58 # Comments: 32` into a
  paragraph doesn't recover the article, it invents one. The exception is a
  feed that already ships a full body in its RSS (Reddit selftext), which is
  summarized directly; the 600-character floor is what stops that from quietly
  re-admitting teasers.
- **Some sources are exempt.** `NWS Florida Alerts` and `NHC Atlantic` publish
  machine-generated alerts where the exact wording *is* the information.
  Paraphrasing a flood warning can only lose the county name.

Every entry in the markdown digest carries a `summary:` marker when it *isn't*
a clean LLM summary — `rss:fetch-error:HTTPStatusError` (Adafruit blocks
non-browser clients), `rss:thin:0` (a Reddit link post with no body),
`llm:rss-body`, `exempt`. That's how a source silently going unfetchable
becomes visible instead of just getting quietly worse.

Two caches, both under `.cache/`: fetched page text (one file per URL) and the
summaries themselves (keyed by model + prompt version + article text). Tuning
the prompt therefore doesn't re-fetch, and re-running doesn't re-summarize.
Bump `PROMPT_VERSION` in `summarize.py` when you change the instructions.

Model defaults to `gpt-5.4-mini` via the Responses API at low reasoning effort;
override with `--summary-model` or `ESP_NEWS_SUMMARY_MODEL`. A run costs a
fraction of a cent and adds ~15 s cold, ~0 s warm.

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

Defaults (`limit=12`, `max_summary=900`) match the firmware's fixed buffers in
`news_client.h` (`NEWS_SUMMARY_LEN 960`), so the device never parses payload it
would only discard. Both were raised from 400/420 in Phase 9 — an LLM summary
trimmed back to 400 characters is a teaser again, which is the whole thing that
phase set out to stop.

### Pointing the firmware at it
In `firmware/src/news_client.h`:

```c
#define NEWS_BASE_URL "http://192.168.1.171:8010"
```

That's this Mac's current LAN address — it can change on DHCP renewal, so a
reserved address or an `.local` hostname is worth setting up if the display is
meant to keep working unattended.

## When the feeds actually update

Fetching RSS only happens when the pipeline runs. Nothing polls on its own, so
there are exactly three ways new stories appear:

| Trigger | What runs |
|---|---|
| `uv run esp-digest` | the full graph, by hand |
| launchd, daily at 08:00 | the same command (see below) |
| BOOT button on the device | `POST /refresh`, which runs the graph in-process |

`feeds.yaml`'s `lookback_hours: 48` is a filter on each fetch, not a schedule —
it decides how far back a run looks, not how often runs happen. Likewise
`NEWS_REFRESH_INTERVAL_MS` (15 min) in the firmware only re-reads the digest the
backend has already written.

### The 08:00 launchd job

`~/Library/LaunchAgents/com.willtobin.esp-news-digest.plist` runs
`uv run esp-digest` in this directory every morning at 08:00.

```bash
launchctl print gui/$UID/com.willtobin.esp-news-digest   # state, run count, last exit
launchctl kickstart -p gui/$UID/com.willtobin.esp-news-digest   # run it now
launchctl bootout gui/$UID/com.willtobin.esp-news-digest        # disable
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.willtobin.esp-news-digest.plist
```

Logs: `~/Library/Logs/esp-news-digest.log` (the digest itself, on stdout) and
`.err` (progress lines — Python's logging writes to stderr, so **content in
`.err` is normal, not a failure**; check the exit code instead).

Two things the plist depends on: `WorkingDirectory` must stay pointed at this
repo, because both uv's project resolution and `load_dotenv()`'s search for
`.env` start from the working directory; and `uv` is invoked by absolute path
(`/opt/homebrew/bin/uv`) because launchd jobs get no login shell and Homebrew is
not on their `PATH`. `RunAtLoad` is deliberately false, so loading the agent or
rebooting doesn't fire an extra run. If the Mac is asleep at 08:00, launchd runs
the job when it next wakes.

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
