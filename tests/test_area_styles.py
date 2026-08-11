"""Every interest area must have a badge in both readers.

This is the test that should have existed already. `interests.yaml`, the Mac
widget's `AreaStyle.table` and the firmware's `AREA_STYLES[]` are three copies
of one list, and three comments asking nicely for them to be kept in sync.
They drifted the moment the profile was rewritten: three areas were renamed and
three added, neither reader was touched, and both fall back to a grey `NEWS`
badge for anything they do not recognise.

That fallback is deliberate and worth keeping — an unknown area should render
un-styled rather than blank. But it fails *silently and plausibly*: nothing
errors, nothing logs, the deck just quietly turns into a wall of identical grey
cards, and the only way to notice is to look at the device and count.

So the sync is asserted here rather than requested in a comment. These parse the
two UI sources as text on purpose: the alternative is a build of each, and a
grep that fails the moment a key is renamed buys most of the value for none of
the cost.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from esp_news.interests import load_interests_profile

REPO = Path(__file__).resolve().parents[1]
SWIFT_THEME = REPO / "desktop" / "Sources" / "ESPNewsWidget" / "Theme.swift"
FIRMWARE_UI = REPO / "firmware" / "src" / "news_ui.cpp"

# Both tables are literal string keys, so the area name appears quoted.
SWIFT_KEY = re.compile(r'"([a-z_]+)":\s*AreaStyle\(label:\s*"([^"]+)"')
FIRMWARE_KEY = re.compile(r'\{\s*"([a-z_]+)",\s*"([^"]+)",\s*0x[0-9A-Fa-f]{6}\s*\}')


def area_names() -> list[str]:
    return [a.name for a in load_interests_profile().areas]


def swift_badges() -> dict[str, str]:
    return dict(SWIFT_KEY.findall(SWIFT_THEME.read_text()))


def firmware_badges() -> dict[str, str]:
    return dict(FIRMWARE_KEY.findall(FIRMWARE_UI.read_text()))


@pytest.mark.parametrize(
    "reader, badges",
    [("Theme.swift", swift_badges), ("news_ui.cpp", firmware_badges)],
)
def test_every_area_has_a_badge(reader, badges):
    """An area with no entry renders as an indistinguishable grey NEWS card."""
    missing = sorted(set(area_names()) - set(badges()))
    assert not missing, (
        f"{reader} has no badge for {missing} — those articles will fall back to "
        f"the grey NEWS badge. Add them there and to the other reader."
    )


@pytest.mark.parametrize(
    "reader, badges",
    [("Theme.swift", swift_badges), ("news_ui.cpp", firmware_badges)],
)
def test_no_badge_for_an_area_that_no_longer_exists(reader, badges):
    """Dead keys are how a rename gets half-done and looks finished."""
    stale = sorted(set(badges()) - set(area_names()))
    assert not stale, f"{reader} styles {stale}, which is not in interests.yaml"


def test_the_two_readers_agree_on_every_label():
    """The device and the widget are meant to be the same deck.

    Colours are checked by eye in the simulator; the labels are checkable here
    and are the half that carries the meaning.
    """
    swift, firmware = swift_badges(), firmware_badges()
    disagree = {
        area: (swift[area], firmware[area])
        for area in swift.keys() & firmware.keys()
        if swift[area] != firmware[area]
    }
    assert not disagree, f"label mismatch (Theme.swift, news_ui.cpp): {disagree}"


def test_labels_fit_the_narrow_badge_column():
    """140 px on the device, and 8 characters is what the widest shipped ones use.

    A label that overflows does not wrap — it is clipped or pushes the score off
    the row, which only shows up on the one card carrying that area.
    """
    too_long = {a: b for a, b in firmware_badges().items() if len(b) > 8}
    assert not too_long, f"badge labels longer than 8 characters: {too_long}"
