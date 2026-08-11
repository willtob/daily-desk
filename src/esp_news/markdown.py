"""The tiny Markdown subset the summaries are allowed to use, and how to remove it.

Phase 9c let the summarizer emit two things and nothing else: ``**bold**`` for
the handful of figures worth spotting at a glance, and ``- `` bullets when the
piece really is a list. Paragraphs are blank-line separated, as they always
were in prose.

The subset is small because the summary has three consumers with wildly
different capabilities, and it has to survive all of them:

* the **Mac panel** renders it properly — SwiftUI parses inline Markdown, and
  bullets are laid out as rows;
* the **ESP32 panel** cannot. An LVGL 8 label has exactly one font, so inline
  bold inside a paragraph is not a matter of effort, it is impossible without
  splitting every paragraph into separate label objects. It strips instead;
* **narration** must never speak syntax. Text-to-speech given ``**four**``
  either reads the asterisks or lands on strange prosody, and this is the
  consumer that would have broken silently — nobody reviews the audio.

So the rule is: emit the subset, and strip it anywhere it cannot be rendered.
``strip_markdown`` is the reference implementation. There are ports of it in
``firmware/src/news_ui.cpp`` and ``desktop/Sources/ESPNewsWidget`` — if the
subset changes here, it changes in all three.
"""

from __future__ import annotations

import re

# ``**bold**``, non-greedy, and only when there is something between the pairs.
# Deliberately not a general emphasis parser: `*single*` is not in the subset,
# so leaving a lone asterisk alone means arithmetic in a summary ("5 * 3")
# survives instead of silently eating the rest of the sentence.
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)

# A bullet marker at the start of a line, with any leading indent.
_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """Remove the allowed Markdown, leaving readable prose.

    Bullets become sentences rather than disappearing: the marker is dropped
    and the line is left as its own paragraph, which is what a list sounds like
    when read aloud.
    """
    if not text:
        return ""
    out = _BOLD.sub(r"\1", text)
    out = _BULLET.sub("", out)
    return out.strip()


def has_markdown(text: str) -> bool:
    """Whether the text uses any of the subset. Cheap check for tests."""
    return bool(text) and (bool(_BOLD.search(text)) or bool(_BULLET.search(text)))
