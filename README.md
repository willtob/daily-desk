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
- [ ] Phase 4 — curate
- [ ] Phase 5 — digest output

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

## Tracing (LangSmith)
Set in `.env`:
```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=esp-news-reporter
```
When unset, tracing is a no-op and the pipeline runs identically.
