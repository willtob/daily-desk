"""Load and validate interests.yaml — the personal interest profile.

Kept separate from feeds.yaml (and from the scoring code) so the profile prose
is easy to tune without touching the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from esp_news.clients.embeddings import DEFAULT_MODEL

# interests.yaml lives at the repo root: src/esp_news/config/interests.py -> parents[3].
DEFAULT_INTERESTS_PATH = Path(__file__).resolve().parents[3] / "interests.yaml"


class InterestArea(BaseModel):
    """One area of interest: prose direction plus concrete reference phrases."""

    name: str
    description: str = ""
    references: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    weight: float = 1.0
    # Added to this area's score for each *other* outlet carrying the same
    # story, up to a few outlets — see nodes/score.py. 0 leaves the area alone.
    coverage_boost: float = Field(default=0.0, ge=0.0)
    # Hard bounds on how many front-page slots this area gets. The max is never
    # exceeded, not even by the backfill or the wildcard; the min reserves slots
    # when the corpus can fill them. Both unset means the CLI's soft cap only.
    max_per_digest: int | None = Field(default=None, ge=0)
    min_per_digest: int | None = Field(default=None, ge=0)
    # An extra instruction handed to the summarizer for articles that won on
    # this area, e.g. "explain what the company does".
    summary_focus: str = ""

    @property
    def reference_texts(self) -> list[str]:
        """Every text embedded for this area.

        The prose description and each reference phrase are embedded separately
        and matched independently, so one sharp phrase can carry an article
        without the long description watering it down.
        """
        texts = [self.description.strip(), *(r.strip() for r in self.references)]
        return [t for t in texts if t]

    @property
    def avoid_texts(self) -> list[str]:
        """Phrases that subtract from this area's score.

        Kept separate from ``reference_texts`` rather than folded in with a sign,
        because the description belongs on the positive side and only the
        explicit ``avoid`` entries belong on the negative one. Empty for most
        areas — an area with no ``avoid`` list scores exactly as it did before
        the field existed.
        """
        return [t for t in (a.strip() for a in self.avoid) if t]


class InterestProfile(BaseModel):
    embedding_model: str = DEFAULT_MODEL
    areas: list[InterestArea] = Field(default_factory=list)

    @field_validator("areas")
    @classmethod
    def _areas_usable(cls, areas: list[InterestArea]) -> list[InterestArea]:
        if not areas:
            raise ValueError("interests.yaml defines no areas — nothing to score against")
        empty = [a.name for a in areas if not a.reference_texts]
        if empty:
            raise ValueError(
                f"interest areas have no description or references: {', '.join(empty)}"
            )
        names = [a.name for a in areas]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate interest area names: {', '.join(sorted(dupes))}")
        return areas

    @property
    def area_limits(self) -> dict[str, int]:
        return {a.name: a.max_per_digest for a in self.areas if a.max_per_digest is not None}

    @property
    def area_floors(self) -> dict[str, int]:
        return {a.name: a.min_per_digest for a in self.areas if a.min_per_digest}

    @property
    def summary_focus(self) -> dict[str, str]:
        return {a.name: a.summary_focus.strip() for a in self.areas if a.summary_focus.strip()}


def load_interests_profile(path: str | Path | None = None) -> InterestProfile:
    """Read interests.yaml into an :class:`InterestProfile`."""
    path = Path(path) if path else DEFAULT_INTERESTS_PATH
    if not path.exists():
        raise FileNotFoundError(f"interest profile not found: {path}")
    return InterestProfile(**(yaml.safe_load(path.read_text()) or {}))
