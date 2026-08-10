# Interest profile — fill-in form

Fill this in whenever you like, in any order. When it's done (or partly done),
it converts straight into `interests.yaml`.

**The most valuable box is the second one.** Real headlines you'd actually open
are worth far more than more description prose — each one becomes its own
reference vector, matched independently. Write them the way a headline reads,
not the way a category reads.

**The third box matters almost as much and nobody thinks to fill it in.** A
headline that *looks* like a match but that you'd skip tells the ranker where
the edge of an interest is. It's also the seed data for dislikes later.

Write in whatever language you'd actually see the headline in — Spanish and
Catalan entries belong here as themselves, not translated.

---

## Part 1 — areas you already have

### ai_open_source
Currently: open-weight releases, permissive licensing, self-hosting tooling.

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1.
  2.
  3.
  4.
  5.
- **Two that look like a match but you'd skip:**
  1.
  2.
- **Anything it's missing:**

### ai_consciousness
Currently: interpretability, steering vectors, model welfare, introspection.

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1. AI model becomes conscious
  2. Interpretability research looks inside LLM brain 
  3. Neural network guardrail that blocks based on emotion vectors
  4. Anthropic interpretability statement
  5. Evolution of consciousness research of AI 
  6. Humans consciousness vs AI consciousnesss
- **Two that look like a match but you'd skip:**
  1. 
  2. 
- **Anything it's missing:**
I also like the relation between human and ai thinking / brain processes

### classic_ml_applied
Currently: CNNs, RNNs, LSTMs, GANs earning their keep in production.

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1. CNN used in gun detection in schools
  2. How an ai architecture works and why it's important
  3. Why transformers are being replaced by world models
  4. Testing different variations of architectures
  5. How Unet models are standard practice for doctors
- **Two that look like a match but you'd skip:**
  1.
  2.
- **Anything it's missing:**
I like learning about model architectures and where they are used. 
### big_tech_career
Currently: hiring, reorgs, layoffs, engineering culture, deep eng blogs.

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1. What big tech employees are saying
  2. How to get hired in a business roll for Google
  3. What it's like working in big tech
  4. What I did to land an internship / job in big tech
  5. Projects that prepared me for working at Whoop. 
- **Two that look like a match but you'd skip:**
  1. How to become a technical SWE
  2.
- **Anything it's missing:**
I want more hiring advice and glimpses into what working in big tech is like. Also, not into very technical roles. I want to use claude code instead of learning how to code in C since I also study business administration. 
### embedded_wearables
Currently: microcontrollers, dev boards, teardowns, low-power design.

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1. ESP32 project ideas
  2. I have built an amazing project that nobody has built before. 
  3. Hacking advice
  4. Optimization of my program
  5. Making a wearable Fitbit myself. 
- **Two that look like a match but you'd skip:**
  1. 
  2.
- **Anything it's missing:**

### startup_vc
Currently: funding rounds, investor trends, pivots and acquisitions.

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1. YCombinator invests in this startup
  2. Startup Hub in Barcelona
  3. Barcelona's district @22 startup scene. 
  4.
  5.
- **Two that look like a match but you'd skip:**
  1. 
  2.
- **Anything it's missing:**
I don't care about specific big name companies

### florida
Currently: Fort Lauderdale and Broward, hurricanes and flooding, Miami tech.

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1. Fort Lauderdale weather alert
  2. Fort Lauderdale shooting
  3. Fort Lauderdale date idea
  4.
  5.
- **Two that look like a match but you'd skip:**
  1. Jacksonville weather alert
  2.
- **Anything it's missing:**
I want more south florida news, not other areas I don't live. 

### spain
Currently: Barcelona city news, Spanish elections/strikes/weather, tech scene.

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1. Tech scene in Barcelona
  2. Barcelona weather alert
  3. Barcelona strike FGC will be at limited capacity 
  4. Sant Cugat news
  5. Gracia, Barcelona news
- **Two that look like a match but you'd skip:**
  1.
  2.
- **Anything it's missing:**
I live in Gracia and commute to sant Cugat so I care most about these areas. 

### barcelona_dates
Currently: restaurants, exhibitions, festivals, rooftops, day trips.

*Note: this area is being buried by scoring, not by a weak profile — see
`ranking-improvements.md`. Still worth filling in, but don't rewrite it on the
assumption it's your fault.*

- **How much do you care (1–5):**
- **Five headlines you'd open without hesitating:**
  1. Date ideas for 'specific weekend' in Barcelona 
  2. Unique date idea in Barcelona
  3. What to do this week in Barcelona
  4. Weird date idea barcelona
  5. Tea ceremony / meditation in Barcelona 
- **Two that look like a match but you'd skip:**
  1. Drive to these mountains near Barcelona 
  2. 
- **Anything it's missing:**
I like artsy dates and love free or cheap events. 
---

## Part 2 — suggested new areas

Based on what's already in your profile, your feeds, and what you actually
spend time building. Delete any that don't land.

### agentic_tooling *(suggested — strongest gap)*
You have an area for AI *models* and none for the layer you personally work in
every day: agent frameworks, orchestration, evals, prompt and context
engineering, coding agents. Your feeds already carry it (Simon Willison, Hacker
News, Import AI) and it currently has to score as `ai_open_source` or lose.

- **Want it? (y/n):**
- **Five headlines you'd open:**
  1. Eval software for langgraph
  2. Learn about AI Engineering
  3. How to use Claude Code better
  4. This is how I do my claude projects
  5. Project ideas for ai engineering 
- **Two you'd skip:**

### edge_inference *(suggested)*
Running models on small hardware — quantization, NPUs, microcontroller ML,
phone-local inference. Sits exactly between `embedded_wearables` and
`ai_open_source`, which is why it currently falls between them: there's one
stray reference about it buried in `classic_ml_applied`. It's also, literally,
what your own project is.

- **Want it? (y/n):**
- **Five headlines you'd open:**
  1. Personal project to make my life better
  2. Advancement in esp32 capabilities
  3. Running ai model on hardware
  4. 
  5.
- **Two you'd skip:**

### landing_the_job *(suggested)*
`big_tech_career` is written as company *news*, but its description says you're
reading it "through the lens of wanting to work at one." Nothing covers the
actual doing: interview loops, system design, levelling, compensation data,
referrals, visa sponsorship.

- **Want it? (y/n):**
- **Five headlines you'd open:**
  1. How to land a job in wearable tech
  2. What I wish I knew at 20 before working in tech
  3. How to balance working in tech and outdoors. 
  4.
  5.
- **Two you'd skip:**

### two_countries *(suggested)*
You live in Fort Lauderdale and Barcelona and nothing covers the seam: direct
flight routes and fares, US-citizen residency and visa rules in Spain, double
taxation, healthcare, shipping, banking. Genuinely consequential and no feed
you have would surface it today — this one probably needs a new source too.

- **Want it? (y/n):** N
- **Five headlines you'd open:**
  1.
  2.
  3.
  4.
  5.
- **Two you'd skip:**

### deep_reads *(suggested, optional)*
A quality bar rather than a topic: long-form worth twenty minutes regardless of
subject. Quanta is already in your feeds. This one is a poor fit for
meaning-distance scoring and a *good* fit for the LLM reranker, so it may be
worth waiting until that lands.

- **Want it? (y/n):** Y
- **Five headlines you'd open:**
  1. Research papers on any of the above topics. 
  2.
  3.
  4.
  5.

---

## Part 3 — anything else

- **Topics you want to see *less* of:** less spain wildfires and Jacksonville weather alerts
- **Sources you trust most, regardless of topic:**
- **Sources you'd rather never see again:**
- **Anything you wish showed up and never does:** more things that teach me about ai engineering and something that is in between my interests as a wildcard
