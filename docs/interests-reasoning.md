# Interest profile — reasoning behind the rewrite

Written 9 August 2026, from the filled-in `docs/interests-form.md`. This is the
audit trail for `interests.yaml`: what I changed, what I inferred rather than
read, and where I could be wrong.

**Read this section first if you read nothing else.** Three things in the new
profile are my judgement and not your answers:

1. **Every weight is a guess.** You left all nine "how much do you care 1–5"
   boxes blank. I inferred them from how much you wrote per area and how
   specific it was. They are the most likely thing here to be wrong. Table and
   reasoning in [Weights](#weights).
2. **`landing_the_job` was merged into `big_tech_career`, not added** — the two
   became one area called `tech_careers`. Argued in [tech_careers](#tech_careers).
3. **`ai_open_source` was demoted to 0.9 and lost both its slots** in today's
   digest as a direct result. It was the only area you left entirely blank, and
   a blank box is genuinely ambiguous — see [the risk note](#the-ai_open_source-risk).

---

## How I read the form

Two things shaped every decision below.

**Scoring is `max`, so references are cheap and one-directional.** An area's
score is the best single match across its references. Adding a reference can
only raise an area's score, never lower it, so where an answer was additive I
added rather than rewrote. Rewriting was reserved for the cases where the
existing prose was actively pointing the wrong way — which happened three times
(`classic_ml_applied`, `big_tech_career`, `barcelona_dates`).

**Your negatives are boundaries, not training data.** You gave three skips and
asked me not to over-fit to them. So: no negative was generalised beyond your
own phrasing, and I measured before adding any. That turned out to matter more
than I expected — see [Negatives](#negatives-the-avoid-mechanism).

---

## Register: your headlines are mostly blog posts

You flagged this and it's the single most cross-cutting thing in the form.
Seven of the headlines you wrote are not news headlines:

> "What I did to land an internship", "I have built an amazing project nobody
> has built before", "This is how I do my claude projects", "What I wish I knew
> at 20", "Projects that prepared me for working at Whoop", "Optimization of my
> program", "Personal project to make my life better"

These are first-person practitioner posts, and register does affect embedding
similarity — a personal build log and a parts announcement about the same board
sit measurably apart in the space even though they share a topic.

**Decision: add voice-matched references alongside the category ones, don't
replace them.** Because scoring is `max`, a first-person reference sitting next
to a categorical one costs nothing — an article matches whichever fits and takes
the better score. Replacing the categorical phrasings would have been the
destructive version of this and would have cost you the actual news in those
areas.

Applied to `agentic_tooling`, `embedded_wearables`, and `tech_careers` — the
three areas where you actually wrote in that voice. Deliberately *not* applied
to `florida` and `spain`, where you wrote news headlines and want news.

---

## Area by area

### ai_open_source
**Decision: unchanged references, weight down to 0.9.**

The only area where you filled in nothing at all — no headlines, no skips,
nothing missing. There was no signal to act on, so the prose stands as written.

The weight is the change, and it's a judgement call with two supports: this area
took 32 of ~110 slots over ten days while being the one you had nothing to say
about, and much of what it was actually winning (coding agents, eval tooling)
now has its own home in `agentic_tooling`. Leaving it at 1.0 would have let feed
volume keep pulling that content back.

<a name="the-ai_open_source-risk"></a>
**This is the riskiest thing I did, and you should check it.** A blank box has
two readings: "I don't care much" or "the description is already right, nothing
to add". I assumed the first. In today's digest the consequence is stark — its
top article, *Glimpses of superintelligence*, was the **#1 pick before** at
0.550 and now scores 0.495, just under the page cutoff, so the area took **zero
slots**. Corpus-wide it still wins 5 articles, so it isn't dead; it just stopped
clearing the bar on a day with no major open-weights release. If the second
reading was the right one, put this back to 1.0 and nothing else needs changing.

### agentic_tooling — new
**Decision: add, weight 1.15.**

Five headlines, and Part 3's "more things that teach me about ai engineering" is
the most direct want anywhere on the form. Weighted highest of anything I set by
preference.

References cover the four things you named — eval tooling for LangGraph, AI
engineering as a subject, getting more out of Claude Code, project ideas — plus
two first-person ones for "This is how I do my claude projects".

Immediate effect: 3 of 10 slots today, including *"Auto mode is now the default
in Claude Code"*, which is about as close to "How to use Claude Code better" as
a real feed is going to produce.

### ai_consciousness
**Decision: add references, weight 1.1.**

The only area where you wrote *six* headlines instead of five, which I read as
engagement. Existing references were already well aimed; four added:

- Human/AI cognition comparison, twice — straight from "I also like the relation
  between human and ai thinking / brain processes", which nothing covered.
- "AI lab publishes a statement or position on model welfare" — for "Anthropic
  interpretability statement". A lab's own position piece reads differently from
  a paper about one.
- "Safety guardrail built from a model's internal representations" — for your
  emotion-vector guardrail headline. The existing steering-vector reference
  covers the mechanism but not the safety application.

### model_architectures
**Decision: rename from `classic_ml_applied`, rewrite the description, keep and
extend the references. Weight 1.1.**

This was mis-scoped and you were right about why. The old description was built
on "pre-transformer" and "why the older architecture was the right call", and
two of your five headlines point the opposite way in time:

> "Why transformers are being replaced by world models"
> "How an ai architecture works and why it's important"

An article about world models replacing transformers would score *against* a
description whose thesis is that the old architectures were the right call.
Renaming was necessary because "classic" was doing load-bearing work in the old
scope; the area is really **architectures, in both directions in time**.

The applied half survives — your CNN-in-schools and U-Net-for-doctors headlines
are exactly what it already did well — and both got dedicated references, since
"computer vision for detection in public spaces" and "U-Net in medical imaging"
are specific enough to be worth their own vectors.

### edge_inference — new, and narrower than proposed
**Decision: add, but scope to ML-on-hardware only. Weight 1.0.**

You filled in three headlines, and two of them aren't edge inference:

| your headline | where I put it | why |
|---|---|---|
| "Personal project to make my life better" | `embedded_wearables` | a maker post, no inference in it |
| "Advancement in esp32 capabilities" | `embedded_wearables` | already the ESP32 reference's job |
| "Running ai model on hardware" | `edge_inference` | the genuinely uncovered thing |

So the new area is built around the third only: quantization, NPUs,
microcontroller ML, on-device inference. It also inherits `"small efficient model
running on embedded or edge hardware"`, the stray reference that had been sitting
in `classic_ml_applied`.

Weight left at 1.0 — one clear headline is not enough to justify more, and a
narrow area with a high weight is how you get one topic dominating.

### embedded_wearables
**Decision: add references, weight 1.1.**

Five headlines, and the area sits low on the raw score scale (median 0.460) for
phrasing reasons rather than preference ones. Added ESP32 project ideas, DIY
fitness tracker, hardware hacking, and two first-person build-log references.

**Ambiguity I had to guess at: "Optimization of my program."** This could be
firmware optimisation (this area) or software/agent workflow optimisation
(`agentic_tooling`). I covered the embedded reading here with "squeezing more
speed or battery life out of my own firmware" and left `agentic_tooling`'s
practitioner references to catch the other. If you meant one specifically, say
which and I'll drop the other.

<a name="tech_careers"></a>
### tech_careers — renamed from `big_tech_career`, with `landing_the_job` merged in
**Decision: rename, invert the role direction, merge, weight 1.1.**

**The inversion.** The old description ended "that's the work I'd be doing"
about deep engineering blog posts. The form says the opposite, four times over:
not into very technical roles, studying business administration, wanting
business roles, and "How to become a technical SWE" listed as a skip. The
description now says business and product roles explicitly, and that
systems-engineering blog posts are *not* this. That last sentence is doing real
work — it's the difference between this area and a generic tech-news area.

**The merge, and why not two areas.** You asked me to decide. Merged, for three
reasons:

1. Once `big_tech_career` is rewritten around getting hired and what the job is
   like, the two areas are describing the same thing. Two of the five headlines
   you wrote *under `big_tech_career`* — "How to get hired in a business roll for
   Google", "What I did to land an internship" — are `landing_the_job` content
   by the form's own definition.
2. Near-duplicate references across two areas make the winning area arbitrary,
   which corrupts the "why it scored" line the digest prints. That line is the
   main tuning instrument in this repo; making it unreliable has a real cost.
3. Splitting one topic across two areas gives it **two per-area caps — six
   slots instead of three**. That inflates its presence for a structural reason
   rather than a preference one, and the per-area cap is already doing more work
   than it should (`ranking-improvements.md` §1).

The residue that isn't about big tech — "How to balance working in tech and
outdoors" — became a reference rather than justifying its own area. It's one
headline.

**Honest note:** this area's best article scored 0.472, under the ~0.52 page
cutoff, so it took no slots despite the weight bump. The feeds didn't carry
hiring-and-career writing at all. That was a **feed supply problem, not a
profile problem**, and no amount of weight fixes it.

*Addressed in the feeds pass:* a new `careers` theme in `feeds.yaml` with
Pragmatic Engineer and Lenny's Newsletter. Both are deliberately
non-engineering. Pragmatic Engineer is weekly, so expect it outside the 48h
lookback most days — this area will stay thin, just no longer empty.

### startup_vc
**Decision: rescope references, weight down to 0.9.**

Three of five headlines, all pointing the same way: YC, Barcelona startup hub,
22@. Plus a narrowing — "I don't care about specific big name companies".

Added YC/accelerator and Barcelona-ecosystem references (the latter in Spanish
too, same cross-language reasoning as `spain`), and rewrote the description
around the scene rather than the scoreboard.

Weight down because 18 slots over ten days is a lot for an area you half-filled
and then narrowed. **Watch this one** — it now wins only 2 articles corpus-wide
and its best is 0.406, which may be an over-correction stacking the weight cut
on top of the rescoping.

**Accepted overlap:** "Barcelona startup ecosystem" appears here *and* "Spanish
tech and startup scene" stays in `spain`, because "Tech scene in Barcelona" was
your #1 headline under `spain`. You named the topic in both areas, so it lives
in both. The cost is the same arbitrary-winner problem I just used to argue
against splitting `tech_careers` — the difference is that here you asked for it
in both places, and there you didn't.

### florida
**Decision: add references (including "things to do"), add one avoid, weight 1.1.**

Weight up: it's where you live, it carries things you'd need to act on, and at
10 picks it was under-represented for that.

Added a Fort Lauderdale crime reference (your headline #2 was "Fort Lauderdale
shooting", and nothing covered it) and two "things to go and do" references.

**On "Fort Lauderdale date idea" appearing under `florida` (your question 7).**
You asked whether that's references added here or a restructure into a
city-agnostic "things to do" area. **References here.** The two date-y areas
cannot share a weight and mean the same thing: `barcelona_dates` carries a
measured ~1.2x handicap from Spanish/Catalan cross-language similarity and from
event listings being specific instances matched against general phrasings.
Florida date content is English and has neither problem. Merging them would put
one weight on two populations that need different ones — and the 1.2x fix from
`ranking-improvements.md` would over-boost the Florida half by exactly the
amount it correctly boosts the Barcelona half. Two areas, two weights, same
underlying interest.

**On geography (your question 6) — the honest answer: references alone can't do
this.** I measured it. Your positive reference "Fort Lauderdale and Broward
County local news" scores the *Jacksonville* thunderstorm alert at **0.516** and
a *Miami/Broward* alert at **0.408** — it ranks them backwards. The embedding
sees "Florida coastal city weather bulletin"; the city name is a small part of a
long, formulaic alert. No amount of positive-reference tuning fixes an ordering
that's already inverted.

What does work is the negative, which is the only reason I implemented one — see
below. Even so, treat Florida-city precision as approximate. The durable fix is
the reranker, which can read "NWS Jacksonville" and just know.

### spain
**Decision: add Gràcia/Sant Cugat references, narrow the wildfire reference
geographically, no avoid entry. Weight 1.0.**

Added the two places you actually live and commute between, by name and in both
languages, plus the FGC/Rodalies commuter rail. **Neighbourhood names have to
appear literally** — a story about Gràcia says "Gràcia", not "a Barcelona
neighbourhood", so there's nothing for a general reference to match on. Your
headline #3 was literally an FGC strike.

**On wildfires (your question 3).** You're right that this is a volume problem
and not a relevance one, and I did not put wildfires in `avoid`. Measured: "wildfire
burning in Spain" scores **0.621** on today's one real fire story — *"Fire in
southeastern Spain active as 470 moved to safety"* — and 0.316 on the next
nearest thing. That's a very sharp reference, which means an avoid entry here
would be a **clean kill switch** on exactly the coverage you'd want during a bad
summer. 470 people evacuated is not something to suppress.

What I did instead: narrowed the Spanish-language reference from "en España" to
"en Cataluña", so fires near you keep scoring and fires 600km away score lower.
The English "major events in Spain" reference still catches the genuinely
national ones. This reduces the reward without removing the coverage.

That's a partial fix and I want to be clear about it. If three wildfire stories
still land on one day, that's the per-area cap letting them, and the honest
answer is the reranker — it can tell "470 evacuated" from "the fifth update on
the same fire", which meaning-distance cannot.

### barcelona_dates
**Decision: remove the day-trip references, add artsy/cheap/offbeat ones,
weight 1.2.**

**The contradiction, resolved by removal rather than by appending.** The area
carried "day trip from Barcelona: beaches, wineries, and towns in Catalonia" and
"escapada de fin de semana desde Barcelona en Cataluña". You listed "Drive to
these mountains near Barcelona" as a skip. Both references are gone.

I want to flag that this is the one place I acted on a negative by deleting
positives, given your "keep it broad" instruction — so here's the second support
that made me comfortable: **not one of the five headlines you wrote for this
area leaves the city.** The skip and the positives agree. That's a scope
correction, not an over-fit to one negative.

Added, from "I like artsy dates and love free or cheap events" and your
tea-ceremony headline: free/cheap plans, offbeat date ideas, quiet unusual
experiences, galleries and independent cinema, plus Spanish and Catalan
equivalents for the free/cheap and cultural-listings ones.

**Weight 1.2 is not a preference guess** — it's the calibration figure from
`ranking-improvements.md` §1, where this area's best score ever recorded (0.437)
sits below `ai_open_source`'s median (0.522). It buys the area the chance to win
on merit; it doesn't hand it a slot.

It worked, and slightly more than intended: 2 slots today, and the *right* two.
The listicle about **wines in other Spanish cities** that took a slot before
dropped out — the description always said travel writing about other cities
wasn't this area, and it now has enough competition for that to bite. What
replaced it is a historic Barcelona shop reopening as a cultural association
HQ, which is the artsy end you asked for.

### deep_reads
**Decision: add to your definition, not the original proposal. Weight 0.95.**

You redefined it — "research papers on any of the above topics" — and that's
what I built: papers and preprints on the profile's own subjects, in an academic
register. The long-form quality bar the area was originally proposed as is gone.
arXiv is already in `feeds.yaml`, so there's supply.

**Honest limitation, and it's a real one.** Your definition makes this a
*register* rather than a subject. A paper about interpretability matches both
this and `ai_consciousness`; the tie is settled by weight, not by meaning. That
shows in today's numbers: **`deep_reads` wins zero articles corpus-wide** — its
best is 0.451 and every candidate is claimed by a topic area first.

I held the weight at 0.95 deliberately rather than raising it until it wins,
because raising it would just relabel `ai_consciousness` articles as
`deep_reads` without changing which articles you see. This area does very little
until the reranker lands, which is where "is this a real paper or coverage of
one" actually belongs. Recorded as built, honestly reported as inert.

### two_countries
Not added. You answered N.

---

<a name="negatives-the-avoid-mechanism"></a>
## Negatives: the `avoid:` mechanism

**Decision: implemented, at λ = 0.15.** I went in expecting to argue for the
opposite, and the measurement changed my mind. Recording both, since the
reasoning is the point.

### What I expected, and why it was wrong

My prior was that negatives can't work here: "Jacksonville weather alert" and
"Fort Lauderdale weather alert" are near-synonyms in embedding space, so a
penalty would hit the wanted article about as hard as the unwanted one, and the
reranker should own this instead.

Measured on the live 173-article corpus, that's false:

| | scores against `"Jacksonville weather alert"` |
|---|---|
| Jacksonville alerts | **0.656, 0.652, 0.531** |
| Broward/Miami alerts | **0.396** |
| phrase-level cosine vs the positive reference | 0.401 |

The negative separates cleanly — and it carries the city signal that the
*positive* reference demonstrably does not (0.516 for Jacksonville vs 0.408 for
Miami, ranked backwards). The negative is doing discrimination the positives
can't. That inverts the argument for leaving it to the reranker.

### Why it's safe enough to ship

Three properties, all tested in `tests/test_scoring.py`:

- **Per-area.** A penalty under `florida` says nothing about the same article's
  `spain` or `agentic_tooling` score. A bad negative damages one area, not the
  profile.
- **Clipped at zero.** An avoid can cost an area the article; it can never drag
  the article's overall score below what another area would have given it.
- **Applied before the weight**, so λ means the same thing in a 1.2x area as in
  a 0.9x one.

### Sizing λ — the sweep

Run against the real corpus and the real new profile:

| λ | Jacksonville alert | "Atlantic hurricane season ramps up" (wanted) |
|---|---|---|
| 0.00 | **on the front page** | #1 in `florida` |
| **0.15** | off the page | #2 — essentially tied with the story above it |
| 0.25 | off the page | #2, gap widening |
| 0.35 | off the page | **#3 — demoted below a routine local shooting** |
| 0.50 | off the page | #4 |

**0.15 is the smallest value that does the job**, which is the property worth
optimising for given your "don't narrow the digest" instruction. I'd initially
set 0.25 on instinct; the sweep says 0.15 is enough and costs less.

The collateral is real and worth naming: the South Florida hurricane-prep story
scores 0.463 against "Jacksonville weather alert", because it *is* a Florida
weather story. At λ=0.15 that costs it 0.069 and it stays near the top of the
area. At 0.35 it drops below a routine shooting, which would be the opposite of
what your description asks for ("Major consequential events first"). That's why
this constant should not be raised without re-running the sweep, and the comment
in `score.py` says so.

### Don't generalise a negative

I tested broadening your phrasing to "Jacksonville, Orlando, Tampa and north
Florida local news" and it was **worse**: it scored 0.415 against the South
Florida hurricane story versus 0.494 against the Jacksonville alert — nearly no
gap. Your narrow, specific phrasing outperformed my generalised version.

This is the mechanism's main failure mode, so it's written into `interests.yaml`
next to the field docs. Negatives stay in your words.

### The three negatives as shipped

| area | avoid entry | what it does today |
|---|---|---|
| `florida` | "Jacksonville weather alert" | **the one doing real work** — evicts the Jacksonville alert |
| `barcelona_dates` | "drive out of Barcelona to the mountains…" | diffuse, top match 0.40; removing the day-trip positives did far more |
| `tech_careers` | "how to become a technical SWE: C, algorithms, LeetCode" | inert, top match 0.32 — recorded as a boundary for when it fires |

Two of three do nothing today. That's the intended state: they're boundaries, and
boundaries should be quiet.

### What the reranker should still own

Not everything went into `avoid`, deliberately:

- **Spain wildfire volume** — a frequency problem, and an avoid entry would be a
  kill switch on consequential coverage.
- **Duplicate/derivative coverage** — the fifth update on the same fire.
- **Florida city precision beyond the one alert type** — reading "NWS
  Jacksonville" and knowing.

---

## Wildcard: mid-pack pool

Implemented `ranking-improvements.md` §4. `DEFAULT_WILDCARD_BAND = (0.40, 0.70)`
replaces the bottom-quartile draw.

The band is expressed as score percentiles and converted to slice indices — the
list is best-first, so the 70th percentile is 30% in from the front and gives
the slice's *start*. Rounding is guarded so a thin corpus can't produce an empty
slice.

**The draw is still uniform random over the band, and it is not seeded.** There
is a test asserting the distribution is flat across the band, precisely so a
future change can't quietly turn this into a ranked pick.

Effect today: the wildcard went from *"Production Imminent: 40 Solar-Charging
Aptera EVs"* at 0.287 to *"Now we have a timeline of the OpenAI accidental
attack against Hugging Face"* at 0.404 — adjacent to your interests without being
central, which is the stated goal.

---

<a name="weights"></a>
## Weights — all provisional, all mine

**You left every 1–5 box blank, so none of these are your answers.** I inferred
them from three things: how many headline slots you filled, how specific and
opinionated the answers were, and how many slots each area already takes.

| area | weight | inferred from |
|---|---:|---|
| `agentic_tooling` | 1.15 | 5 headlines + the only explicit "I want more of this" in Part 3 |
| `barcelona_dates` | 1.20 | **not a preference guess** — the calibration figure from `ranking-improvements.md` §1 |
| `ai_consciousness` | 1.10 | wrote six headlines where five were asked for |
| `model_architectures` | 1.10 | 5 specific headlines + "I like learning about model architectures" |
| `embedded_wearables` | 1.10 | 5 headlines; low raw scale (median 0.460) for phrasing reasons |
| `tech_careers` | 1.10 | 8 headlines across two merged areas + "I want more hiring advice" |
| `florida` | 1.10 | where you live, carries actionable emergencies, under-picked at 10 slots |
| `edge_inference` | 1.00 | only one of three headlines was actually about it |
| `spain` | 1.00 | heavily engaged (5 specific headlines) but already over-picked at 26 slots — net neutral |
| `deep_reads` | 0.95 | new, unproven, structurally overlaps other areas |
| `ai_open_source` | 0.90 | the one area left entirely blank, taking the most slots (32) |
| `startup_vc` | 0.90 | half-filled and explicitly narrowed; over-picked at 18 slots |

Two of these are load-bearing enough to call out again: **`ai_open_source` at
0.90** (see the risk note above) and **`startup_vc` at 0.90**, which may be an
over-correction now that the references were narrowed as well.

If you want to give me the 1–5 numbers, they'd replace this whole table and I'd
map them onto roughly 0.85–1.25, keeping `barcelona_dates` at its calibrated
value regardless — that one is correcting a measurement artefact, not a
preference.

---

## Ambiguities and things I guessed at

1. **The blank `ai_open_source` box** — "don't care much" or "already right"? I
   assumed the former. Highest-impact guess in the file.
2. **"Optimization of my program"** — firmware or software? Covered both, in
   `embedded_wearables` and `agentic_tooling`.
3. **`edge_inference` and `deep_reads` "Want it? (y/n)"** were left blank but had
   headlines filled in. I read filled-in headlines as yes. (`two_countries` was
   an explicit N and `deep_reads` an explicit Y further down.)
4. **"Projects that prepared me for working at Whoop"** — I read Whoop as an
   example of a wearables company rather than a specific employer, and wrote
   "landing a job at a hardware or wearables company". If Whoop specifically
   matters, that should be its own reference.
5. **"Hacking advice"** — read as hardware hacking / reverse engineering, given
   it sat under `embedded_wearables`. If you meant security or CTF work, that's
   a different area and no feed currently carries it.
6. **"What big tech employees are saying"** — read as employee sentiment and
   candid accounts, not as executive statements.
7. **"Startup Hub in Barcelona" and "district @22"** were treated as one
   interest, since 22@ *is* the startup hub.

---

## What I deliberately did not do

- **Did not narrow beyond your three negatives.** No negative was generalised;
  the one time I tried, it measured worse.
- **Did not delete the wildfire references** — narrowed them geographically
  instead, for the reasons above.
- **Did not touch the cross-language references or their rationale.** The
  comments explaining why Spanish and Catalan phrasings exist are unchanged, and
  new Spanish/Catalan references were added under the same reasoning.
- **Did not implement calibration** (`ranking-improvements.md` §1). Still the
  right fix; the hand-set weights here are the "cheap interim" that doc
  describes, and they'll need redoing whenever feeds or references change.
- **Did not seed the wildcard.**

---

## Watch list for the next few digests

- `ai_open_source` at zero slots — over-correction, or correct?
- `tech_careers` and `deep_reads` win nothing. For `tech_careers` I think that's
  feed supply; for `deep_reads` it's structural.
- `startup_vc` down to 2 corpus-wide wins.
- `spain` fell from 3 slots to 1 today. Partly the wildfire narrowing, mostly
  new areas competing. If it keeps falling, the narrowing went too far.
