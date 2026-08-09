# News Display — ESP32-S3-Touch-LCD-3.49

Touch news reader for the scored digest produced by
`~/dev/esp-news-reporter`. Portrait 172 × 640.

Lives in the same repo as the backend it talks to. Built on the same driver
layer as the `PomodoroTimer` / `NotionDisplay` projects under
`~/Desktop/ESP32/ESP32-S3-Touch-LCD-3.49/` — see [../docs/HARDWARE.md](../docs/HARDWARE.md)
for the board notes, and
[CLAUDE.md](CLAUDE.md) for the architecture and the rules worth not
rediscovering.

## Simulator — use this for UI work

There is a desktop LVGL build in [`sim/`](sim). It compiles the real
`src/news_ui.cpp` against the real `include/lv_conf.h` at the same 172 × 640
geometry, so the window matches the panel. A flash cycle is ~23 s plus walking
over to the board; this is ~2 s.

```bash
brew install sdl2      # one-time
cd sim
make run               # window: mouse = touchscreen, B = BOOT, S = screenshot
make shot              # headless -> shot.bmp
```

To view a render as an image (LVGL writes BMP, most tools want PNG):

```bash
./sim --shot shot.bmp && sips -s format png shot.bmp --out shot.png
```

Reach screens that need input without touching anything:

```bash
./sim --shot out.bmp --scroll 900              # scroll the list first
./sim --shot out.bmp --tap 120                 # open the card at y=120
./sim --shot out.bmp --tap 120 --swipe right   # open a story, swipe back
./sim --shot out.bmp --tap 120 --swipe left    # open a story, go to the next
```

`--tap` runs the animation out before the shot, so it can only ever show where
a transition *ends* — which is the part that was never in doubt. To see the
transition itself, `--tap-hold` returns as soon as the click is delivered and
`--settle N` advances N frames of 20 ms:

```bash
./sim --shot mid.bmp --tap-hold 300 --settle 4   # 4 frames into the expand
```

Two flags for checking animations by measurement rather than by eye —
`--geom` prints the detail view's rect every frame, and `--time` prints what
each frame costs:

```bash
./sim --shot x.bmp --geom --tap-hold 300 --settle 30   # per-frame travel
./sim --shot x.bmp --time --tap-hold 300 --settle 12   # per-frame cost
```

`--geom` exists because you cannot read a transition off the pixels: the deck
behind it is the same tint as the card, and its header rule and ledges sit at
exactly the edges you would be trying to measure, so a scanline finds the
chrome and reports a confident wrong answer. Ask the object where it is.

`--film PREFIX` writes every frame from that point on, which is the only way to
catch a one-frame flicker — `--settle` cannot reach inside a tap, because the
press and release run within `inject_tap`:

```bash
./sim --shot x.bmp --film /tmp/f/open --tap-hold 300 --settle 28
```

The sim uses **two full-screen buffers with `full_refresh = 1`**, matching
`lvgl_port.c`. It used to use a single 172×80 partial buffer, which draws the
same pixels by a different path — partial mode redraws invalidated rectangles,
full refresh redraws everything and swaps buffers — so ordering bugs around
hide/move/invalidate could appear on the board and not here. Keep these in
step with the driver.

The sample articles cover the cases that break layout — a very long title, a
one-word title, an accented Spanish headline, an empty summary, and an
interest area missing from `AREA_STYLES`.

## Build & flash

```bash
~/.platformio/penv/bin/pio run            # compile
~/.platformio/penv/bin/pio run -t upload  # flash
~/.platformio/penv/bin/pio device monitor # serial @ 115200
```

Current size: **RAM ~32%, Flash ~21%**.

Don't set `upload_port` — auto-detection matches the board by USB VID:PID and
a glob is passed to esptool verbatim, which breaks every upload. "No serial
data received" usually means the board isn't enumerated at all; check
`ls /dev/cu.usbmodem*` before assuming a fault.

## The three views

This is the Mac widget's card deck, rebuilt in LVGL for the 172×640 panel —
same layout, same wallpaper palette, same interest-area labels. See
[`../desktop/README.md`](../desktop/README.md) for the deck's design and
[`CLAUDE.md`](CLAUDE.md) for what LVGL could not reproduce.

**DECK** — fixed header (`NEWS` + status), one story at a time as a card in
its interest area's colour, three coloured ledges behind it for the rest of
the digest, and a nav bar with `‹`, a counter and `›`. The card shows the area
as a badge, the score as a number and as a bar mapping the 0.25–0.60 cosine
band across the card, the headline, a short excerpt and the source. Flip with
the nav buttons or by swiping left and right.

**LIST** — swipe up. The whole digest as a scrollable column, best-scoring
first, in the same card styling but with headlines wrapped in full rather than
clipped to fit a fixed card. This is where you go when the deck's card cut off
the part of the headline that said what the story was. Swipe right (or down)
to go back.

**DETAIL** — tap any card, from either view. Full untruncated title at 20 pt,
source and exact score, the summary in a scrollable body, and `LISTEN` pinned
at the bottom so it is reachable without paging to the end. Swipe right to go
back — to whichever view you opened it from — or swipe left for the next story.

The card **expands into** the story: the detail view starts at the tapped
card's exact rect, in its colour and at its corner radius, and grows to fill
the panel. Swiping back shrinks it into whichever card it came from, wherever
that card has since scrolled to. It used to slide in from the right, which read
as arriving at a different screen instead of opening the thing you touched —
on a panel 172 px wide that thread is worth more than it is on a phone. Paging
from one story to the next still slides sideways, because there is no card on
screen for it to grow out of.

Touch drag scrolls; LVGL suppresses the click when a press becomes a drag, so
scrolling past a card never opens it.

The **BOOT** button (GPIO 0) is wired as: *back* when a story is open,
*manual refresh* on the deck or the list. It is the widget's ↻ button.

That refresh is the real thing — it asks the backend to re-run the whole
pipeline (`POST /refresh`), waits for it by polling `/health`, then re-fetches.
It takes 20-30 s, and the header reads "refreshing..." throughout. Simply
re-fetching `digest.json` would be near-instant but would redraw the same
stories, because the backend rewrites that file only when the pipeline runs —
otherwise just once a day, from the launchd job at 08:00.

## Data source

`NEWS_BASE_URL` in `src/news_client.h` is the backend root, shared by the digest
and audio modules so host and port are written once. An empty string makes the
firmware load built-in sample articles instead, so the UI is fully usable with
no network at all.

```c
#define NEWS_BASE_URL "http://192.168.1.171:8010"
```

Port 8010, not 8000 — Docker holds 8000 on the Mac. Plain `http://` only; an
`https://` endpoint would need `WiFiClientSecure` wired into
`news_refresh_once()`.

### JSON contract

Field names match the Python `Article` model in esp-news-reporter exactly, so
the backend can serialise curated articles without renaming anything:

```json
{
  "articles": [
    {
      "title": "...",
      "summary": "...",
      "source": "Hackaday",
      "matched_area": "ai_open_source",
      "score": 0.4213
    }
  ]
}
```

Articles are rendered in the order given — the backend's curate node owns
sorting. At most `NEWS_MAX_ARTICLES` (12) are kept; the rest are ignored.
Polling interval is 15 min (`NEWS_REFRESH_INTERVAL_MS`).

`matched_area` drives the badge colour. Recognised values, from
`interests.yaml`:

| area | badge | colour |
|---|---|---|
| `ai_open_source` | OPEN SRC | green |
| `ai_consciousness` | INTERP | purple |
| `classic_ml_applied` | CLASSIC | teal |
| `big_tech_career` | BIG TECH | blue |
| `embedded_wearables` | EMBEDDED | orange |
| `startup_vc` | STARTUP | yellow |
| `florida` | FLORIDA | coral |
| `spain` | SPAIN | pink |

An unrecognised area falls back to a grey `NEWS` badge, so adding an area to
`interests.yaml` degrades gracefully instead of breaking the display. To give
it real styling, add a row to `AREA_STYLES[]` in `src/news_ui.cpp`.

## Structure

Only these files are project-specific — everything else is the copied
Waveshare driver layer and must not be rewritten:

```
src/news_ui.cpp/.h       both views, all LVGL widgets, 250 ms refresh
src/news_client.cpp/.h   HTTP + ArduinoJson polling task, sample data
src/main.cpp             init order
```

`src/lvgl_port.c` has the usual single edit: `news_ui_create()` in place of
`pomodoro_ui_create()`. Its `lvgl_port_set_rotation()` no longer rebuilds the
UI — this project is portrait-only and nothing calls it.

## LVGL notes

Widgets are created once in `news_ui_create()` (called under the LVGL lock by
`lvgl_port_init()`); everything after that happens in `ui_timer_cb`, which
LVGL also runs under the lock. No other module touches an `lv_obj_t`.

All 12 cards are built up front and shown/hidden with `LV_OBJ_FLAG_HIDDEN` —
hidden children are skipped by the flex layout, so the list closes up on its
own. This keeps widget creation out of the render path entirely.

Unlike the pomodoro UI, which clears `LV_OBJ_FLAG_SCROLLABLE` everywhere,
`list_body` and `detail_body` deliberately enable it.

Fonts available in `lv_conf.h`: montserrat 14, 16, 20, 36, 44. **Not** 18, 22
or 24 — using one that's disabled fails at link time.

If you raise `NEWS_MAX_ARTICLES` much past 12, watch `LV_MEM_SIZE` (48 KB) in
`include/lv_conf.h`: each card is 6 widgets plus its label text.
