# News Display — ESP32-S3-Touch-LCD-3.49

Touch news reader for the scored digest produced by
`~/dev/esp-news-reporter`. Portrait 172 × 640.

Built on the same driver layer as `PomodoroTimer` / `NotionDisplay` — see
`~/Desktop/ESP32/esp32-projects/HARDWARE.md` for the board notes, and
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

## The two views

**LIST** — fixed header (`NEWS` + status) over a scrollable column of cards,
best-scoring story first. Each card shows the interest area as a coloured
badge, the score, a 3-line title (ellipsised), the source, and a score bar
whose width maps the 0.25–0.60 cosine band to the full card width.

**DETAIL** — tap any card. Full untruncated title at 20 pt, source and exact
score, then the summary in a scrollable body. `BACK` returns to the list.

Touch drag scrolls; LVGL suppresses the click when a press becomes a drag, so
scrolling past a card never opens it.

The **BOOT** button (GPIO 0) is wired as: *back to the list* when a story is
open, *force a refresh* when already on the list.

## Data source

`NEWS_URL` at the top of `src/news_client.cpp` is **empty by default**, which
makes the firmware load 8 built-in sample articles so the UI is fully usable
before the backend exists. Point it at the Phase 6 endpoint to go live:

```c
#define NEWS_URL "http://192.168.1.42:8000/digest.json"
```

Plain `http://` only — an `https://` endpoint needs `WiFiClientSecure` wired
into `news_refresh_once()`.

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
