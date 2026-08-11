"""Grading an explanation against the topic's ``covers`` hint.

Runs on the Responses API with a pydantic ``text_format``, the same client and
the same shape as ``summarize.py``: a model override in the environment, a
``PROMPT_VERSION`` with a changelog comment per bump, and every call logged
with what produced it. The version is not cosmetic — the whole point of this
module is that the rubric gets iterated on, and a stored grade is meaningless
if you can't tell which rubric produced it. It is written to every row in
``grades`` for exactly that reason.

**Why the prompt is this long.** The failure mode of LLM graders is not
harshness or leniency, it is vagueness — "good overview, could go deeper" on
every explanation regardless of content, and a score that drifts a point or two
between identical runs. Three things are doing the work against that:

  * The ``covers`` hint from topics.yaml is the checklist. ``missed_concepts``
    has to come from it, so "you didn't mention initialisation sensitivity"
    replaces "you could have said more".
  * The score bands are anchored to what is *present*, not to an impression.
  * Three worked examples on one topic — weak, middling, strong — pin the
    scale. One topic rather than three so the model sees exactly what separates
    a 3 from a 6 from a 9, with the subject matter held constant.

Errors propagate rather than being swallowed. ``summarize.py`` falls back to
the RSS text because one dead article must not take down a digest; here there
is one call and someone is waiting on it, so a failure has to be visible.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from openai import OpenAI
from pydantic import BaseModel, Field

from esp_news.embeddings import MissingAPIKeyError
from esp_news.learn.topics import Topic

logger = logging.getLogger(__name__)

# Same family as the summarizer, and overridable the same way. Grading is a
# harder task than summarizing, so this is the knob to reach for first if the
# feedback reads as shallow — try a larger model before rewriting the rubric.
DEFAULT_LEARN_MODEL = os.getenv("ESP_NEWS_LEARN_MODEL", "gpt-5.4-mini")

# Reasoning tokens bill against this ceiling too, and grading is the kind of
# task that uses them, so it clears the visible output by a wide margin.
_MAX_OUTPUT_TOKENS = 4000

# Grading benefits from deliberation in a way summarizing does not — the model
# has to check an explanation against a checklist rather than compress prose.
_REASONING_EFFORT = "medium"

# Explanations longer than this are truncated before being sent. Fifteen minutes
# of typing does not reach it; fifteen minutes of speech transcript might.
_MAX_EXPLANATION_CHARS = 24000

# Bump when the rubric, the bands, or the calibration examples change.
#
# 1: First rubric. Ten-point bands anchored on what the explanation contains,
#    missed_concepts constrained to the topic's `covers` hint, three calibration
#    examples on k-means (weak/middling/strong).
PROMPT_VERSION = "1"


class Grade(BaseModel):
    """The structured grade. This is the API response shape too."""

    score: int = Field(ge=1, le=10)
    feedback: str
    missed_concepts: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class GradeResult:
    """A grade plus what produced it, for the store and the log."""

    grade: Grade
    model: str
    prompt_version: str
    latency_ms: int


_RUBRIC = """\
You are grading someone's from-memory spoken-or-written explanation of a technical \
topic. They had fifteen minutes and no notes. You are not grading writing quality, \
length, or polish — only whether the substance of a correct explanation is present.

You will be given the topic, a checklist of what a good explanation covers, and \
their explanation.

SCORING BANDS. Anchor on what is actually present, not on the impression the \
writing gives:
  1-2   Nothing correct beyond restating the name, or a fundamental misconception \
that would mislead a listener.
  3-4   Knows what the thing is for, but no working mechanism — describes purpose \
and applications where the checklist asks how it works.
  5-6   The core mechanism is there and correct, but incomplete: significant \
checklist items missing, or stated without explaining why.
  7-8   The core mechanism is correct and reasonably complete, with the reasoning \
behind it. Minor gaps or one missing item. This is a good explanation.
  9-10  Complete and correct, including the limitations, tradeoffs or edge cases \
in the checklist, and explains why rather than only what. A 10 additionally shows \
insight beyond the checklist — a connection, a consequence, a good analogy.

Confident fluency is not knowledge. An explanation that sounds authoritative while \
omitting the mechanism scores in the 3-4 band, not the 6-7 band. Conversely, halting \
or badly organised prose that contains the right substance scores on the substance.

MISSED_CONCEPTS. Draw these from the checklist. Each entry names one specific thing \
that was absent or wrong, in a few words, phrased so they know what to go and learn \
— "didn't say centroids move to the mean of their assigned points", not "incomplete \
explanation of the algorithm". Never pad this list with things outside the checklist \
just to have entries. If the explanation genuinely covers everything, return an empty \
list. If something was stated *incorrectly* rather than omitted, say so explicitly \
and give the correction.

STRENGTHS. Each entry must point at something they actually said. If you cannot tie \
a strength to specific content in the explanation, leave it out — an empty list is \
better than "showed good understanding". Do not manufacture encouragement.

FEEDBACK. Two to four sentences addressed to them directly. Lead with the single \
highest-value thing to fix and say why it matters to understanding the topic, not \
just that it was missing. No preamble, no summary of what they wrote, no closing \
encouragement.
"""

# One topic, three quality levels. Held constant so the bands are calibrated
# against explanation quality rather than against topic difficulty.
_CALIBRATION = """\
Three worked examples. Same topic each time, so the difference between them is \
entirely the quality of the explanation.

CALIBRATION TOPIC: k-means clustering
CHECKLIST: Alternating between assigning points to the nearest centroid and moving \
centroids to the mean of their assignments, until stable. Should name the real \
limitations: k chosen in advance, convergence to a local optimum that depends on \
initialisation (hence k-means++), and an implicit assumption of round, \
similarly-sized clusters.

--- EXAMPLE 1 ---
EXPLANATION: "K-means is a clustering algorithm, so it's unsupervised — there are no \
labels, you're just finding structure. You give it your data and a number k, and it \
groups the data into k clusters where similar points end up together. People use it \
for things like customer segmentation, where you want to find natural groups of users \
without knowing in advance what the groups are."
GRADE:
{
  "score": 3,
  "feedback": "This says what k-means is for but never says what it does — there is no \
step in here that a computer could execute. The thing to learn is the two-step loop: \
assign every point to its nearest centroid, then move each centroid to the mean of the \
points assigned to it, and repeat until nothing moves. Everything else about k-means, \
including why it can give you different answers on different runs, follows from that \
loop.",
  "missed_concepts": [
    "the assign-then-update iteration that is the actual algorithm",
    "that centroids move to the mean of their assigned points",
    "that the loop repeats until assignments stop changing",
    "that k must be chosen in advance, and that this is a real limitation",
    "sensitivity to initialisation and convergence to a local optimum",
    "the assumption that clusters are roughly round and similarly sized"
  ],
  "strengths": [
    "correctly placed it as unsupervised, and said why — no labels, structure only",
    "gave a concrete application in customer segmentation"
  ]
}

--- EXAMPLE 2 ---
EXPLANATION: "You pick k and drop k centroids somewhere in the data. Then you repeat \
two steps. First, assign each data point to whichever centroid is closest, usually by \
Euclidean distance. Second, move each centroid to the average position of all the \
points that got assigned to it. Those two steps keep alternating, and you stop when the \
assignments stop changing between rounds, which means the centroids have stopped moving \
too. The clusters you end up with are the final assignments."
GRADE:
{
  "score": 6,
  "feedback": "The mechanism is correct and complete — the loop, both steps, and the \
stopping condition are all right. What is missing is everything about when it goes \
wrong, and that is the half that decides whether you would actually reach for k-means. \
Start with initialisation: because you dropped those centroids somewhere, two runs on \
identical data can converge to different clusterings, which is a local optimum problem \
and the reason k-means++ exists.",
  "missed_concepts": [
    "convergence is to a local optimum, so different initialisations give different results",
    "k-means++ as the standard fix for initialisation sensitivity",
    "that k has to be chosen in advance, with no principled way to know it",
    "the implicit assumption of round, similarly-sized clusters, and what it breaks on"
  ],
  "strengths": [
    "described both steps of the loop correctly, including the mean update",
    "gave a precise stopping condition — assignments no longer changing",
    "named Euclidean distance rather than leaving 'closest' undefined"
  ]
}

--- EXAMPLE 3 ---
EXPLANATION: "K-means alternates two steps until it settles. You initialise k centroids, \
assign every point to the nearest one, then recompute each centroid as the mean of the \
points assigned to it, and repeat. Both steps reduce the same objective — total squared \
distance from points to their assigned centroid — so it always converges, but only to a \
local optimum. Where it converges depends entirely on where you started, which is why \
k-means++ spreads the initial centroids out instead of picking at random. Two other \
things bite in practice. You have to pick k up front, and there's no principled way to \
do it — the elbow method is a heuristic on a curve that usually has no elbow. And \
because the assignment step is 'nearest centroid', the decision boundaries are linear \
and the implied cluster shape is a round blob of roughly equal size. Give it two \
elongated crossing bands and it will cut them into four pieces, because a round blob is \
the only shape it can express."
GRADE:
{
  "score": 10,
  "feedback": "Complete, and the reasoning is right rather than just the facts. The \
strongest part is deriving the round-cluster limitation from the assignment rule instead \
of listing it — that is the difference between having read about k-means and \
understanding it. Nothing here needs fixing.",
  "missed_concepts": [],
  "strengths": [
    "derived the round-blob assumption from 'nearest centroid' rather than asserting it",
    "named the shared objective and used it to explain why convergence is guaranteed",
    "separated guaranteed convergence from convergence to a good answer",
    "was honest about the elbow method being a heuristic rather than a solution",
    "gave a concrete failure case in the crossing elongated bands"
  ]
}
"""

_INSTRUCTIONS = f"{_RUBRIC}\n\n{_CALIBRATION}"


class Grader:
    """Grades explanations against a topic's ``covers`` hint."""

    def __init__(self, model: str = DEFAULT_LEARN_MODEL) -> None:
        self.model = model
        self._client: OpenAI | None = None

    def _openai(self) -> OpenAI:
        if self._client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise MissingAPIKeyError(
                    "OPENAI_API_KEY is not set — add it to .env (see .env.example)."
                )
            self._client = OpenAI()
        return self._client

    def grade(self, topic: Topic, explanation: str, *, source: str = "text") -> GradeResult:
        """Grade one explanation. Raises on an empty explanation or an API failure.

        ``source`` is carried for the log and the stored row rather than shown to
        the model: the rubric is about substance, and telling the grader it is
        reading a transcript would invite it to make allowances for speech that
        it does not make for typing. Same rubric, both ways in.
        """
        explanation = (explanation or "").strip()
        if not explanation:
            raise ValueError("nothing to grade — the explanation is empty")

        if len(explanation) > _MAX_EXPLANATION_CHARS:
            logger.warning(
                "Explanation truncated from %d to %d chars",
                len(explanation),
                _MAX_EXPLANATION_CHARS,
            )
            explanation = explanation[:_MAX_EXPLANATION_CHARS]

        prompt = (
            f"TOPIC: {topic.name}\n"
            f"DIFFICULTY: {topic.difficulty}\n"
            f"CHECKLIST: {topic.covers.strip()}\n\n"
            f"EXPLANATION:\n{explanation}\n\n"
            f"Grade it now."
        )

        started = time.perf_counter()
        response = self._openai().responses.parse(
            model=self.model,
            instructions=_INSTRUCTIONS,
            input=prompt,
            text_format=Grade,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            reasoning={"effort": _REASONING_EFFORT},
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        grade = response.output_parsed
        if grade is None:
            # A refusal or a truncated response. Explicit, because the alternative
            # is an AttributeError three frames away from the cause.
            raise RuntimeError(
                "the grader returned no parsable grade "
                f"(model={self.model}, prompt_version={PROMPT_VERSION})"
            )

        # Everything needed to reconstruct why a grade came out as it did. The
        # rubric will be iterated on; without this the old grades become
        # uninterpretable rather than merely superseded.
        logger.info(
            "Graded %s: score=%d model=%s prompt=v%s source=%s latency=%dms "
            "missed=%d strengths=%d",
            topic.id,
            grade.score,
            self.model,
            PROMPT_VERSION,
            source,
            latency_ms,
            len(grade.missed_concepts),
            len(grade.strengths),
        )

        return GradeResult(
            grade=grade,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
        )
