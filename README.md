# ESP News Reporter

Personalized tech news digest. A LangGraph pipeline that pulls articles from RSS
feeds, scores them against a personal interest profile using embedding similarity
(the "fitness function"), and renders a curated markdown digest.

v1 is CLI-only and runs on-demand. See [esp-news-plan.md](esp-news-plan.md) for the
full build plan.

## Status
- [x] Phase 0 — repo & environment setup
- [x] Phase 1 — ingest node (fetch + filter RSS)
- [ ] Phase 2 — dedup / normalize
- [ ] Phase 3 — score (fitness function)
- [ ] Phase 4 — curate
- [ ] Phase 5 — digest output

## Setup
Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then add your keys
```

## Feeds
Feeds are configured in [feeds.yaml](feeds.yaml), grouped by theme
(embedded/wearables, big tech, startup/VC). Edit freely.

## Running Phase 1 (ingest)
```bash
uv run esp-ingest              # uses the lookback window from feeds.yaml
uv run esp-ingest --hours 72   # override the lookback window
```
Prints article counts per source and per theme.

## Tracing (LangSmith)
Set in `.env`:
```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=esp-news-reporter
```
When unset, tracing is a no-op and the pipeline runs identically.
