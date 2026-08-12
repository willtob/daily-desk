"""Every interest area must have a badge in the widget.

This is the test that should have existed already. `interests.yaml` and the
Mac widget's `AreaStyle.table` are two copies of one list, and a comment
asking nicely for them to be kept in sync. They drifted the moment the
profile was rewritten: three areas were renamed and three added, the reader
wasn't touched, and it falls back to a grey `NEWS` badge for anything it
does not recognise.

That fallback is deliberate and worth keeping — an unknown area should render
un-styled rather than blank. But it fails *silently and plausibly*: nothing
errors, nothing logs, the deck just quietly turns into a wall of identical grey
cards, and the only way to notice is to look at the widget and count.

So the sync is asserted here rather than requested in a comment. This parses
the Swift source as text on purpose: the alternative is a build, and a grep
that fails the moment a key is renamed buys most of the value for none of
the cost.

The ESP32 firmware had a matching `news_ui.cpp` table and used to be checked
here too, but it's retired and no longer built from this repo — it moved to
``~/Desktop/ESP32/NewsReporter/``.
"""

from __future__ import annotations

import re
from pathlib import Path

from esp_news.config.interests import load_interests_profile

REPO = Path(__file__).resolve().parents[1]
SWIFT_THEME = REPO / "desktop" / "Sources" / "ESPNewsWidget" / "Theme.swift"

# A literal string key, so the area name appears quoted.
SWIFT_KEY = re.compile(r'"([a-z_]+)":\s*AreaStyle\(label:\s*"([^"]+)"')


def area_names() -> list[str]:
    return [a.name for a in load_interests_profile().areas]


def swift_badges() -> dict[str, str]:
    return dict(SWIFT_KEY.findall(SWIFT_THEME.read_text()))


def test_every_area_has_a_badge():
    """An area with no entry renders as an indistinguishable grey NEWS card."""
    missing = sorted(set(area_names()) - set(swift_badges()))
    assert not missing, (
        f"Theme.swift has no badge for {missing} — those articles will fall "
        f"back to the grey NEWS badge. Add them there."
    )


def test_no_badge_for_an_area_that_no_longer_exists():
    """Dead keys are how a rename gets half-done and looks finished."""
    stale = sorted(set(swift_badges()) - set(area_names()))
    assert not stale, f"Theme.swift styles {stale}, which is not in interests.yaml"


def test_labels_fit_the_narrow_badge_column():
    """8 characters is what the widest shipped ones use.

    A label that overflows does not wrap — it is clipped or pushes the score
    off the row, which only shows up on the one card carrying that area.
    """
    too_long = {a: b for a, b in swift_badges().items() if len(b) > 8}
    assert not too_long, f"badge labels longer than 8 characters: {too_long}"
