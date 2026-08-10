# Ranking improvements — backlog

Written 8 August 2026, after the performance work closed. Everything here is
about *what gets picked*, not how fast it runs.

## The evidence this is based on

Score distributions pulled from the 10 digests in `digests/`:

| area | picks | min | median | max | refs | feed volume |
|---|---|---|---|---|---|---|
| ai_open_source | 32 | 0.426 | **0.522** | 0.694 | 6 | high |
| startup_vc | 18 | 0.482 | 0.516 | 0.606 | 4 | med |
| spain | 26 | 0.425 | 0.502 | 0.619 | 9 | high |
| big_tech_career | 3 | 0.482 | 0.493 | 0.524 | 6 | high |
| florida | 10 | 0.437 | 0.484 | 0.499 | 5 | med |
| ai_consciousness | 7 | 0.474 | 0.484 | 0.557 | 6 | med |
| embedded_wearables | 6 | 0.410 | 0.460 | 0.574 | 5 | med |
| classic_ml_applied | 5 | 0.422 | 0.440 | 0.553 | 6 | med |
| barcelona_dates | 3 | 0.425 | **0.431** | 0.437 | 11 | ~235 articles |

Read the top and bottom rows together. `barcelona_dates` has the most
reference vectors of any area, five dedicated feeds returning ~235 articles,
and Spanish/Catalan phrasing added deliberately to counter cross-language
weakness. Its **best score ever recorded (0.437) is below `ai_open_source`'s
median (0.522)**.

So it can never win a slot on merit. It has appeared 3 times in 10 days, and
only because the per-area cap of 3 pushed better-scoring articles aside.

Two things follow:

1. The per-area cap is silently doing the job the weights were meant to do.
2. Ranking currently tracks *how much my feeds publish about a topic*, not
   *how much I care about it*. AI, Spain and startups take 76 of ~110 slots.

Note: all nine weights are still `1.0`. Nothing has been tuned yet.

---

## 1. Per-area calibration — highest value

**Problem.** Raw cosine similarity is not comparable across areas. Different
reference texts sit at different points on the scale for reasons that have
nothing to do with preference:

- **Language.** `text-embedding-3-small` is English-dominant. Spanish and
  Catalan text scores systematically lower even against Spanish references.
  `barcelona_dates` and half of `spain` are affected.
- **Specificity.** A reference is a *category* ("new restaurant opening in
  Barcelona"); an article is an *instance* ("Roig obre a Gràcia"). General-to-
  specific similarity is lower than general-to-general. Event listings suffer;
  topic essays don't.

**Fix.** Score each area against its own distribution rather than in absolute
terms — roughly `(similarity - area_mean) / area_std`, so the question becomes
"how unusually good is this *for this area*". Establish the mean and spread per
area from a background sample of articles, cached and recomputed rarely.

**Payoff.** Weights go back to meaning pure preference, and the per-area cap
goes back to being a diversity nicety instead of load-bearing.

**Cheap interim.** Set weights by hand to close the gap — `barcelona_dates`
needs roughly 1.2x to reach parity with `ai_open_source`. Crude, works today,
but has to be redone whenever feeds or references change. Calibration is the
version that stays correct.

> **Status, 9 Aug 2026 — interim done, real fix outstanding.** Weights are now
> hand-set across all twelve areas, `barcelona_dates` at the 1.2x above. The
> reasoning is in `interests-reasoning.md`. Everything in this section still
> stands: those weights are a snapshot that mixes preference and calibration
> together, and they go stale the moment references change. Note the table below
> predates the profile rewrite — the areas no longer all match.

---

## 2. LLM reranker — the biggest quality change

**Problem.** Meaning-distance answers "is this about something I said I like."
It cannot see whether an article is any good, whether it's original reporting
or a rewrite of yesterday's story, or whether it's specific enough to act on.
A thin aggregator post and a deep investigation on the same topic look nearly
identical to it.

**Fix.** Two-stage, the standard retrieve-then-rerank shape:

1. Embeddings cut ~500 deduped articles down to ~40. Cheap, keeps the current
   scorer exactly as-is.
2. An LLM reads those 40 with the interest profile in prose and picks the final
   10, with a one-line reason each.

**Cost.** ~40 articles x (title + 300 chars) is a few thousand tokens. One or
two calls, a cent or two, a few seconds. Negligible next to the summarize node.

**Fits what's already there.** The digest already prints a "why it scored"
line. A written reason is strictly better than a number, and the reason is what
makes it debuggable when a pick looks wrong.

**Watch for.** Position bias (shuffle the candidate order), and don't let it
see the embedding score or it'll anchor on it.

---

## 3. Likes / dislikes

Planned separately. Notes so the design doesn't paint itself into a corner:

- Compare articles to **what was chosen**, not only to what was written in
  `interests.yaml`. Both signals should survive — the written profile is what
  handles cold start and new areas.
- **Don't train anything at first.** Under ~50 labels, average the liked
  articles' vectors and compare against that. A learned model on 1536
  dimensions with 30 examples fits noise. Revisit around 100+ labels.
- Liked articles become long-lived reference vectors, read every run. The
  embedding cache evicts on **last used, never on created-at** — this is what
  keeps them alive. Guarded by tests in `tests/test_embeddings.py`.
- Dislikes are more informative per label than likes and much rarer. Consider
  weighting them higher.

> **Related, 9 Aug 2026.** The *written* half of dislikes now exists: an optional
> `avoid:` list per area in `interests.yaml`, scored as
> `max(0, best_reference - 0.15 * best_avoid)` before the area weight. It is
> per-area and clipped, so a bad negative costs one area rather than the
> profile. Sized against a live corpus — see `interests-reasoning.md` for the
> lambda sweep and for why generalising a negative measured *worse* than the
> user's own narrow phrasing. This is unrelated to the *learned* dislikes above
> and doesn't constrain them; it's cold-start written preference, same as
> `references`.

---

## 4. Wildcard pool — done, 9 Aug 2026

The wildcard used to draw from the **worst-scoring quarter**. That was mostly
sports scores and crime blotter: randomness without discovery. The pleasant
surprise more likely lives mid-pack — adjacent to an interest but not central.

Now draws from the 40th–70th percentile: `DEFAULT_WILDCARD_BAND` in
`nodes/curate.py`. Same dice, different bucket. **The draw is still uniform
random over the band and is not seeded** — there is a test in
`tests/test_scoring.py` asserting the distribution stays flat, so this can't
quietly become a ranked pick later.

---

## Considered and rejected

Recorded so they don't get re-raised:

- **Hand-built feature scoring** (source reputation, length, recency as a
  linear model) — more knobs, less signal than the reranker.
- **Fine-tuning an embedding model** — nowhere near enough labels, and it would
  need redoing constantly.
- **Dropping embeddings for keyword rules** — loses cross-language matching,
  which `spain` and `barcelona_dates` depend on.
- All performance work. That's closed: see git history for parallel ingest,
  the feed cache, and the SQLite embedding cache.

---

## Suggested order

1. Weights by hand — ten minutes, unblocks `barcelona_dates` today.
2. Reranker — the real quality jump.
3. Calibration — makes weights honest, and matters more as areas are added.
4. Likes/dislikes — best done after the reranker, so there's something good to
   express a preference *about*.
