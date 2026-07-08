# Personalized Tech News Digest — Build Plan (v1)

## Goal
A LangGraph pipeline that pulls articles from RSS feeds, scores them against my personal
interests using embedding similarity (the "fitness function"), and outputs a curated,
personalized markdown digest to the terminal. Runs manually, on-demand, on my Mac.
No FastAPI serving, no ESP32, no TTS in v1 — those are explicitly future phases.

Fitness function themes to weight toward (v1):
- Embedded hardware / wearables
- Big tech company news
- Startup / VC & industry business

(Not weighted in v1, add later if desired: general AI/ML research & agents)

---

## Architecture (v1 scope only)

```
RSS feeds ──▶ [ingest_node] ──▶ [dedup_node] ──▶ [score_node] ──▶ [curate_node] ──▶ [digest_node] ──▶ terminal/markdown output
```

State shape (rough):
```python
class Article(BaseModel):
    title: str
    url: str
    source: str
    published: datetime
    summary: str          # raw RSS summary/description
    content: str | None    # optional full-text if fetched
    embedding: list[float] | None
    score: float | None

class DigestState(BaseModel):
    raw_articles: list[Article]
    deduped_articles: list[Article]
    scored_articles: list[Article]
    curated_articles: list[Article]
    digest_markdown: str | None
```

---

## Phase 0 — Repo & environment setup
- New repo, separate from Agentic Edge work (per NDA/IP separation)
- `uv` or `venv` + `langgraph`, `pydantic`, `feedparser`, `openai` (or chosen embedding provider), `numpy`
- `.env` for API keys (embedding provider, TTS later)
- `feeds.yaml` — config file listing RSS feed URLs, tagged by theme (hardware/wearables,
  big tech, startup/VC)

**Checkpoint:** repo scaffolded, dependencies installed, feeds.yaml has real feed URLs.

---

## Phase 1 — Ingest node
- Given `feeds.yaml`, fetch each feed with `feedparser`
- Parse into `Article` objects (title, url, source, published, summary)
- Filter to articles published in the last N hours/days (configurable, since this runs
  on-demand rather than on a schedule)

**Checkpoint:** run this node standalone, print count of articles pulled per source.

---

## Phase 2 — Dedup/normalize node
- Strip HTML from summaries
- Dedupe near-identical stories across sources (title similarity or URL canonicalization
  is enough for v1 — don't over-engineer with embeddings here, save that for scoring)

**Checkpoint:** run standalone, confirm duplicate stories from different outlets collapse
into one.

---

## Phase 3 — Score node (the fitness function)
This is the core of the project.

- Build a **personal interest profile**: a short set of reference texts/phrases describing
  what I care about within each theme (embedded/wearables, big tech news, startup/VC).
  Embed these once, store as reference vectors.
- Embed each article's title + summary
- Score = cosine similarity between article embedding and the profile vector(s)
  (can do per-theme scores and take max, or weighted average — pick one and iterate)
- Attach `score` to each `Article`

**Personalization needed here:** the interest profile text is what makes this "mine."
Write 2-4 sentences per theme describing specifically what makes an article interesting
to me (e.g. what about big tech news do I care about — layoffs? product launches?
research orgs? What about startup/VC — funding rounds in AI/hardware specifically?
company strategy?). This is worth writing deliberately rather than defaulting to generic
phrasing.

**Checkpoint:** run standalone on a batch of articles, sanity-check that high-scoring
articles actually look interesting and low-scoring ones don't.

---

## Phase 4 — Curate node
- Sort by score, take top N (configurable, e.g. top 10)
- Optionally cluster/group by theme for readability in the digest

**Checkpoint:** confirm top N looks like a reasonable "front page."

---

## Phase 5 — Digest/output node
- Render curated articles into clean markdown: headline, source, one-line why-it-scored
  (optional), link
- Print to terminal AND write to a timestamped file (`digests/2026-07-08.md`) so there's
  a running log to look back on and eventually use for evaluating/improving the fitness
  function

**Checkpoint:** full graph runs end-to-end, manual invocation (`python run_digest.py`)
produces a real, personalized digest.

---

## Future phases (not in v1 — do not build yet)
- **Phase 6:** FastAPI endpoint to serve the latest digest JSON
- **Phase 7:** ESP32-S3 fetch + e-ink display UI/UX
  — when this phase starts, reference my existing ESP32 wiring project (I'll provide it)
  to reuse the same GPIO/pin conventions rather than guessing
- **Phase 8:** TTS layer (ElevenLabs or OpenAI TTS) to read digest aloud, paired with an
  I2S amp (e.g. MAX98357A) if audio output is added to the ESP32 build
- **Phase 9:** Learned fitness function — log actual engagement (click/read/save) and
  retrain scoring weights instead of relying on a static profile

---

## Instructions for the coding agent
- Build and test each phase standalone before wiring into the LangGraph graph — don't
  write the whole graph at once.
- Keep the interest-profile text in its own config/prompt file, not hardcoded, so it's
  easy for me to tune.
- Ask me for the actual RSS feed URLs per theme before hardcoding `feeds.yaml` — don't
  invent sources.
- Ask me for the interest-profile description text per theme before writing Phase 3 —
  this is the personalization step, don't default to something generic.
- Stop and confirm with me before starting any future phase (6+) — v1 ends at Phase 5.
