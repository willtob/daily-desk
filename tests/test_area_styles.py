"""Every interest area must have a badge in the widget.

`interests.yaml` and the widget's `AreaStyle.table` are two copies of one list,
and two comments asking nicely for them to be kept in sync. They drifted the
moment the profile was rewritten: three areas were renamed and three added,
neither reader was touched, and an unrecognised area falls back to a grey `NEWS`
badge.

That fallback is deliberate and worth keeping — an unknown area should render
un-styled rather than blank. But it fails *silently and plausibly*: nothing
errors, nothing logs, the deck just quietly turns into a wall of identical grey
cards, and the only way to notice is to look at it and count.

So the sync is asserted here rather than requested in a comment. This parses the
Swift source as text on purpose: the alternative is a build, and a grep that
fails the moment a key is renamed buys most of the value for none of the cost.

This was a three-way check while the ESP32 panel had its own `AREA_STYLES[]` in
`news_ui.cpp`. The panel is retired (see the `firmware-final` tag) and those
cases are gone with it, including the one asserting labels fit in 8 characters —
that was the 140 px badge column on the 172×640 panel, and nothing here is
width-constrained the same way. If a length limit is wanted for the widget it
needs measuring against this layout, not inheriting from that one.
"""

from __future__ import annotations

import re
from pathlib import Path

from esp_news.interests import load_interests_profile

REPO = Path(__file__).resolve().parents[1]
SWIFT_THEME = REPO / "desktop" / "Sources" / "ESPNewsWidget" / "Theme.swift"

# The table is literal string keys, so the area name appears quoted.
SWIFT_KEY = re.compile(r'"([a-z_]+)":\s*AreaStyle\(label:\s*"([^"]+)"')


def area_names() -> list[str]:
    return [a.name for a in load_interests_profile().areas]


def swift_badges() -> dict[str, str]:
    return dict(SWIFT_KEY.findall(SWIFT_THEME.read_text()))


def test_every_area_has_a_badge():
    """An area with no entry renders as an indistinguishable grey NEWS card."""
    missing = sorted(set(area_names()) - set(swift_badges()))
    assert not missing, (
        f"Theme.swift has no badge for {missing} — those articles will fall back "
        f"to the grey NEWS badge. Add them to AreaStyle.table."
    )


def test_no_badge_for_an_area_that_no_longer_exists():
    """Dead keys are how a rename gets half-done and looks finished."""
    stale = sorted(set(swift_badges()) - set(area_names()))
    assert not stale, f"Theme.swift styles {stale}, which is not in interests.yaml"
