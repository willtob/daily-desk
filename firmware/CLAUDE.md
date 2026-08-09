# News Display — ESP32-S3-Touch-LCD-3.49

> **Doing UI or animation work here? Read
> [WORKING-NOTES.md](WORKING-NOTES.md) first.** This file is what the code is;
> that one is how work on it goes wrong. Short version: measure on the board
> before changing anything, keep the sim's buffer setup matching the driver,
> verify with assertions rather than screenshots, and flash one behavioural
> change at a time.

Touch news reader for the 172×640 portrait panel. Fetches a scored, curated
digest over HTTP from the `esp-news-reporter` backend (`~/dev/esp-news-reporter`),
shows it as a deck of cards, and reads articles aloud through the ES8311 codec.

The UI is a port of the Mac widget in [`../desktop/`](../desktop/) — same deck,
same palette, same interest-area labels. See
[Three views](#three-views-and-how-you-move-between-them) for the two places
the port had to diverge, both forced by what LVGL 8 can draw.

This firmware lives at `~/dev/esp-news-reporter/firmware/` — the same repo as
the Python backend it talks to, since they are one project (the backend's
`esp-news-plan.md` calls this Phase 7).

Board notes: [`../docs/HARDWARE.md`](../docs/HARDWARE.md) — pins and addresses
only, deliberately not a second copy of the full board notes, which stay at
`~/Desktop/ESP32/esp32-projects/HARDWARE.md`. Same driver layer and
conventions as the sibling projects at
`~/Desktop/ESP32/ESP32-S3-Touch-LCD-3.49/{PomodoroTimer,NotionDisplay}`.

**LVGL 8.4.0 is not vendored here.** It stays with the Waveshare checkout at
`~/Desktop/ESP32/ESP32-S3-Touch-LCD-3.49/Arduino_Libraries/lvgl8` (~40 MB,
shared with the sibling projects) and is referenced by absolute path from
`platformio.ini` and `sim/Makefile`. On another machine, repoint those two.

---

## Iterate in the simulator, not on the board

**Read this before changing anything visual.** There is a desktop LVGL build in
`sim/`. Use it. A flash cycle is ~23 s plus physically looking at the panel; the
simulator is ~2 s and can be screenshotted.

```bash
cd sim
make            # build
make run        # window: mouse = touchscreen, B = BOOT button, S = screenshot
make shot       # headless -> shot.bmp, no window
```

### Seeing the UI from a Claude session

LVGL writes BMP; the Read tool needs PNG. Convert, then read the image:

```bash
cd sim && ./sim --shot shot.bmp && sips -s format png shot.bmp --out shot.png
# then: Read the absolute path to shot.png
```

This is the whole point of the simulator — it lets an agent *look* at the
layout instead of computing font metrics and guessing. Do it after any UI
change, before flashing.

### Reaching screens that need input

```bash
./sim --shot out.bmp --swipe left              # flip the deck to the next card
./sim --shot out.bmp --swipe up                # open the list
./sim --shot out.bmp --swipe up --scroll 700   # ...and scroll it down 700 px
./sim --shot out.bmp --tap 300                 # tap the top card -> its story
./sim --shot out.bmp --tap 148,616             # tap the nav bar's next button
./sim --shot out.bmp --tap 300 --swipe right   # open a story, then swipe back
./sim --shot out.bmp --tap 300 --swipe left    # open a story, then next story
./sim --shot out.bmp --boot                    # BOOT -> "refreshing..." state
./sim --shot out.bmp --mem                     # ...and report lv_mem usage
```

**Actions run in the order they are written**, so one shot can drive a whole
path: `--swipe up --tap 200 --swipe right` is "open the list, open its second
card, swipe back" — which is the only way to check that back lands on the list
rather than on the deck. Grouping them by kind instead turns that into a
different journey and yields a plausible screenshot of the wrong screen.

`--tap` takes `y`, or `x,y` when the column matters. Centre-x is right for
cards but never hits the nav bar, whose buttons sit at the edges with the
counter between them.

`--swipe` interpolates the drag over several indev reads, because LVGL needs
movement spread across polls to classify it as a gesture rather than a click.

`--mem` prints what the built UI costs out of `LV_MEM_SIZE`. Worth checking
before and after adding widgets — see [LVGL rules](#lvgl-rules) for why
running out does not look like an error.

### How the simulator is wired

- Builds the **real** `src/news_ui.cpp` against the **real** `include/lv_conf.h`
  at the same geometry and colour depth, so the window matches the panel.
- `sim/stubs/sim_stubs.c` implements `news_client`, `news_audio` and
  `wifi_manager` against their **real headers** — so the sim cannot drift from
  the device API without failing to compile. Change a header, the sim breaks,
  which is the intended behaviour.
- Sample articles deliberately include the cases that break layout: a very long
  title, a one-word title, an accented Spanish headline, an empty summary, and
  an `area` that is missing from `AREA_STYLES`.
- LVGL 8 has no built-in SDL backend; the flush and pointer callbacks are
  implemented directly in `sim_main.c` rather than pulling in `lv_drivers`.
- Header dependencies come from `-MMD -MP` (a `.d` file per object). Without
  them, changing a constant that alters `sizeof(news_article_t)` recompiled the
  stubs while `news_ui.o` kept the old struct stride — the UI then read the
  article array at the wrong offsets and rendered **convincing garbage**
  instead of failing to build. If the sim ever shows scrambled fields, suspect
  a stale object and `make clean` before believing anything on screen.
- `lv_conf.h` additionally gets an explicit `$(OBJS):` edge, because it must
  invalidate everything on the first build too, before any `.d` file exists —
  enabling a font otherwise leaves a stale empty object and the link fails with
  an undefined symbol. Both that rule and the `-include` must come **after** the
  `sim:` rule: a target listed earlier becomes make's default goal.
- Requires SDL2: `brew install sdl2`.

---

## Build & flash

```bash
~/.platformio/penv/bin/pio run            # compile
~/.platformio/penv/bin/pio run -t upload  # flash
~/.platformio/penv/bin/pio device monitor # serial @ 115200
```

Size as of the last change: **RAM 25.1%** (82,276 of 327,680 bytes), flash
21.4%. That is *lower* than the 40.1% before the deck, because moving LVGL's
widget pool to PSRAM also removed the 48 KB static pool that used to sit in
internal RAM — see
[Two memory traps](#two-memory-traps-that-cost-a-flash-cycle-each). Of what
remains, 11.5 KB is `news_articles[]` — 12 × `NEWS_SUMMARY_LEN`, which Phase 9
raised from 420 to 960 bytes at a measured cost of exactly 6480 bytes.

**Do not set `upload_port`.** Auto-detection matches the board by USB VID:PID
(303a:1001) and is correct whenever it is plugged in. A glob like
`/dev/cu.usbmodem*` is passed to esptool verbatim — globs are not expanded —
and every upload then fails with "port is busy or doesn't exist". A fixed path
is also wrong: the port renumbers between plugs (`usbmodem101` → `usbmodem1101`).

**"No serial data received" almost always means the board is not enumerated**,
not that it is faulty. Auto-detection then falls back to any serial device
present (a Bluetooth speaker, on this Mac). Check `ls /dev/cu.usbmodem*` first;
if absent, reseat the cable or force the ROM bootloader (hold BOOT, tap RESET,
release BOOT).

---

## Three views, and how you move between them

```
        ┌──────────────────────────────────────┐
        │                DECK                  │  the digest as a card stack
        │   one card + three coloured ledges   │  ‹ › or swipe left/right
        └───┬───────────────────────────┬──────┘
   swipe up │                           │ tap the card
            ▼                           ▼
        ┌────────────────┐        ┌──────────────┐
        │      LIST      │──tap──▶│    DETAIL    │
        │ all 10, scroll │◀─swipe─┤ full story + │
        └────────────────┘  right │    LISTEN    │
            │  swipe right/down   └──────────────┘
            ▼                        swipe right
          DECK                    returns to whichever
                                  view opened it
```

| Gesture / control | Deck | List | Detail |
|---|---|---|---|
| swipe left | next card | — | next story |
| swipe right | previous card | back to deck | back to origin |
| swipe up | open list | — | — |
| swipe down | — | back to deck | — |
| tap card | open story | open story | — |
| `‹` `›` | flip the deck | — | — |
| BOOT | rebuild digest | rebuild digest | back to origin |

**Back is origin-aware.** A story opened from the list returns to the list, not
to the deck — `detail_from` records which view opened it. Getting this wrong is
invisible in a single screenshot and obvious in use.

**Horizontal is the reliable axis on the two scrolling views.** LVGL suppresses
a gesture once a press has been classified as a scroll, and near the top of a
list a downward drag is usually taken as an elastic scroll. That is why swipe
*right* is the documented way out of the list, matching the detail view, with
down accepted as a bonus when it does come through. The deck has nothing
scrollable in it, so all four directions are dependable there.

**Gestures and clicks are not alternatives.** LVGL sends `LV_EVENT_GESTURE`
while the finger moves and still sends `CLICKED` when it lifts, so swiping up
on a card ran `show_list()` and then had the card's own handler open the story
on top of it. `cb_gesture` calls `lv_indev_wait_release()`, which ends the
press so no click follows.

**A swipe that starts on bare background needs the shell to be clickable.**
LVGL hit-tests only `LV_OBJ_FLAG_CLICKABLE` objects to decide whose gesture a
press is; with a non-clickable background there is no target, no event and
nothing to bubble. `cont_deck` and `cont_list` carry the flag with no click
handler for exactly this.

## Project structure

Only these are project code. Everything else is the copied Waveshare driver
layer — **do not rewrite `lvgl_port.c`, `i2c_bsp.c`, or `drv/`**.

```
src/news_ui.cpp/.h        all three views, every LVGL widget, 250 ms refresh
src/news_client.cpp/.h    HTTP + ArduinoJson digest polling
src/news_audio.cpp/.h     narration playback (streams PCM to I2S)
src/audio_beep.cpp/.h     ES8311 + I2S bring-up, copied from PomodoroTimer
src/wifi_manager.cpp/.h   Wi-Fi association
src/main.cpp              init order
src/fonts/                generated Montserrat — see Type scale, do not hand-edit
tools/gen_fonts.sh        regenerates src/fonts/
sim/                      desktop LVGL build (see above)
```

`lvgl_port.c` carries the usual single edit: `news_ui_create()` in place of
`pomodoro_ui_create()`. Its `lvgl_port_set_rotation()` no longer rebuilds the
UI — this project is portrait-only and nothing calls it.

**Wi-Fi credentials live in `src/wifi_credentials.h`, which is gitignored.**
`wifi_credentials.example.h` is the tracked template. Never inline SSID or
password into `wifi_manager.cpp`.

---

## Init order (`main.cpp`) — load-bearing

```
1. i2c_master_Init()      first; audio borrows the bus 0 handle
2. lvgl_port_init()       display + LVGL + touch; calls news_ui_create() inside the lock
3. lcd_bl_pwm_bsp_init()  backlight only after the display is up
4. wifi_manager_init()    own task
5. news_client_init()     own task
6. news_audio_init()      ES8311 + I2S + narration task
```

`loop()` does nothing; all work is in FreeRTOS tasks.

---

## Shared state

Cross-task data is plain `volatile` globals with one writer and one reader —
no mutexes. The UI **never** blocks; it sets a request flag and returns.

| Symbol | Written by | Read by |
|---|---|---|
| `news_articles[]`, `news_count` | `news_task` | UI |
| `news_data_version` | `news_task` | UI (change detection) |
| `news_audio_playing` / `_index` / `_failed` | `audio_task` | UI |
| `news_rebuilding` | `news_task` | UI (status line) |

**The `data_version` pattern:** the fetcher fills the buffers, then bumps
`news_data_version` *last*. `ui_timer_cb` compares against the version it last
rendered and only re-renders on change. Never render off `news_count` alone.

---

## LVGL rules

Widgets are created **once**, in `news_ui_create()`, which `lvgl_port_init()`
calls while holding the LVGL lock. Everything after that happens in
`ui_timer_cb` (250 ms), which LVGL also runs under the lock. No other module
touches an `lv_obj_t`. To add a feature: add a `volatile` flag, set it from
wherever, read it in `ui_timer_cb`.

Specifics that are easy to undo by accident:

- **The widget pool lives in PSRAM on the device** (`LV_MEM_CUSTOM 1` +
  `heap_caps_malloc(MALLOC_CAP_SPIRAM)`), and that is not an optimisation —
  see [Two memory traps](#two-memory-traps-that-cost-a-flash-cycle-each). The
  simulator keeps the built-in pool so `--mem` can still measure.
- **Running out of LVGL memory does not look like an error.** LVGL's
  `LV_USE_ASSERT_MALLOC` handler is a `while(1)`, so exhausting the pool stops
  the firmware dead inside `news_ui_create()` with the backlight already on —
  which looks exactly like a display driver fault — and hangs the simulator on
  the same line. The built UI measures 65,904 bytes at rest
  (`sim --shot x.bmp --mem`), so the stock 48 KB and even 64 KB cannot build
  it. If either ever hangs at startup, check `--mem` first.
- All 12 list cards are built up front and shown/hidden with
  `LV_OBJ_FLAG_HIDDEN`. Hidden children are skipped by the flex layout, so the
  list closes up on its own and no widget is ever created in the render path.
- `list_body` and `detail_body` **deliberately enable** `LV_OBJ_FLAG_SCROLLABLE`.
  The pomodoro UI clears it everywhere; that is wrong here.
- **Build order is z-order.** The deck, then the list, then the detail. The
  list rides up over the deck, so building it first put it underneath an
  opaque full-screen deck, where it animated into place perfectly and was
  never visible — indistinguishable from the swipe not being detected. It is
  fixed at build time rather than with `lv_obj_move_foreground()` so the child
  indices stay constant for `sim_main.c`'s `--scroll`.
- **List** titles use `LV_LABEL_LONG_WRAP` at `LV_SIZE_CONTENT`; **deck** card
  titles are `LV_LABEL_LONG_DOT` in a fixed box, because every ledge in the
  stack has to line up with the card above it. That difference is the reason
  the list still exists: at 140 px a 5-line clip is ~75 characters, and the
  list is where you go when the clip lost the part that says what the story
  is. Do not "fix" the list to match the deck.
- The deck flip is one `lv_anim` driving a 0..256 progress that every moving
  part reads, rather than one animation per property per object — five
  independently interruptible anims leave the stack skewed when a flip is cut
  short. `step_deck()` settles any flip still in flight before starting the
  next, so a fast double press steps twice instead of stranding a card.
- View transitions animate `x` (or `y` for the list) via `lv_anim`;
  `lv_anim_del` runs before each start so a fast double-swipe cannot stack.
- Opening a story is a **geometry expansion, not a slide**. `cont_detail` is a
  full-screen container whose children are absolutely positioned, and LVGL
  clips children to their parent's rectangle — so `expand_cb()` starting it at
  the tapped card's `lv_obj_get_coords()` rect, at `CARD_RADIUS`, in the card's
  own tint, and growing it to fill the panel is the entire transition. Position,
  size and radius only: no transform, no opacity, therefore no intermediate
  layer (see below for why that matters), and no measurable cost on top of the
  whole-panel blit `full_refresh` already does every frame.
  - The origin is stored as an `lv_obj_t *` and its coordinates are read fresh
    at collapse time, so a list scrolled since opening still shrinks back to
    the right card.
  - `d_topbar` is deliberately **transparent**, not painted in the story's
    tint. `cont_detail` behind it is already that colour, and an opaque child
    would square off the two rounded top corners for the whole animation —
    LVGL clips to the parent's rectangle, not to its rounded shape.
  - The lateral slide survives only for detail → detail paging, where there is
    no card on screen to grow from.

### The panel dims until you pick it up

`imu_wake.cpp`. Three board facts make it cheap, and one of them is a trap:

- The **QMI8658** IMU sits at `0x6B` on I2C port 0 and `i2c_bsp.c` **already
  registers `imu_dev_handle`** for it. No new bus, no new device, no library —
  `i2c_read_buff()` is all it takes.
- **Touch is on port 1, the IMU on port 0.** The obvious worry when adding a
  second I2C reader here is the touch controller, and there is nothing to
  guard. Polling still runs on an `lv_timer` so it is serialised anyway.
- **The backlight duty is inverted.** `lcd_bl_pwm_bsp.h` defines its levels as
  `0xff - n`, so the brightest setting is duty `0` and `255` is off. Passing a
  percentage straight to `setUpduty()` darkens the panel when you ask for
  bright.

The metric is the *change* in the acceleration vector between samples, not its
magnitude: at rest an accelerometer reads ~1 g whatever its orientation, so
magnitude says nothing, while tilting redistributes gravity across the axes.
L1 norm, because it needs no square root and the threshold is empirical.

Both halves were measured on the board rather than guessed:

| | motion metric |
|---|---|
| at rest on a desk | 25–145 typical, worst spike **345** |
| being picked up | p25 523, median 4243, p75 9380, peak **32068** |

The useful finding is that this is **bimodal** — moving reads in the thousands,
still reads in the tens, and almost nothing lands between 300 and 1200. So the
threshold is insensitive across that whole range (500 catches 75% of pick-up
samples, 1200 catches 68%), and the right move is the largest margin that costs
nothing rather than the smallest number that works. `WAKE_MOTION` is 1200, i.e.
3.5× the worst resting spike.

Idleness is motion *and* `lv_disp_get_inactive_time()`, so touch and BOOT keep
it lit for free, and it will not dim mid-narration.

### The UI flips when the board is turned over

Same file, same accelerometer sample — orientation is a *gravity* question, not
a gyro one. A gyro measures rotation rate, which is zero once you have finished
turning the board; where it has settled is what the display needs to know.

**Never call `lvgl_port_set_rotation()` from LVGL code.** `lvgl_mux` is a plain
`xSemaphoreCreateMutex()`, which is **not recursive**, and `imu_wake_poll()`
runs from an `lv_timer` — i.e. already inside the lock. Taking it twice hangs
the display task permanently, with no output to say why. Use
`lvgl_port_set_rotation_locked()` from anything running under `lv_timer_handler`
or an event callback; the locking version is for other tasks.

Only 0↔180 is wired up. Same logical resolution means `apply_rotation()` can
skip `lv_disp_drv_update()` and just invalidate the active screen — the update
call would re-fire `LV_EVENT_SIZE_CHANGED` and dirty every layout for a size
that did not change, which is the state churn that has broken deck scroll and
selection before. The invalidate is **not** optional: with `full_refresh` the
panel is only written when something is dirty, so a flip on a static screen
would otherwise not show up until the next digest poll redrew something.

The axis mapping is a physical fact about the PCB and was read off the board:

| position | ax | ay | az |
|---|---|---|---|
| held vertical | −1593 | **−16713** | −590 |
| resting on a stand | +900 | −3900 | **+14500** |

Gravity is entirely on Y standing up and moves to Z laid back, so **Y is the
640 px edge, Z is the screen normal, X is the 172 px edge**. The sign is the
half that is easy to get backwards, and it does not fail quietly — guessing
`+1` inverted the display in the position the board actually lives in. `ay` is
negative the normal way up, so `ORIENT_UP_SIGN` is `-1`.

`ORIENT_MIN` is a quarter g, just above the measured resting 3900, so the panel
has no opinion on its stand. Sitting near the threshold is harmless: crossing
it only lets the panel *agree with the rotation it already has*, and flipping
needs a reading past it with the opposite sign.

### The frame budget, measured

Do not guess at this and do not tune `LV_DISP_DEF_REFR_PERIOD` hoping it helps.
Timed on the board by counting `lv_anim` exec callbacks against `millis()`:

| animation | frames | wall time | per frame |
|---|---|---|---|
| deck flip (`FLIP_MS` 280) | 5 | 350 ms | **70 ms** |
| detail expand (`EXPAND_MS` 400) | 11 | 410 ms | **37 ms** |

So the panel runs animations at **14–27 fps**, and the refresh period is *not*
the limit — it is already 30 ms and neither animation reaches it. The limit is
render time plus a 220 KB full-panel blit (`full_refresh = 1`, every frame,
whatever changed). Lowering the period only queues work that cannot be
delivered; that is what wedged the display the one time it was tried.

The flip is the *slower* of the two despite moving less, because `apply_slot()`
drops the faces' opacity below 255 and LVGL renders anything translucent
through a layer. Two cards in flight, two layers, every frame.

**The lever is therefore distance-per-frame, not cost.** At 14–27 fps, LVGL's
stock easings are actively wrong: they are shaped for 60 fps, where a
front-loaded curve reads as responsiveness. `lv_anim_path_ease_out` over 6
frames puts **36.5%** of the whole distance in the first one, then 26, 17, 10,
5, 4 — a lurch followed by a crawl. On the deck flip that is invisible because
the card only moves 74 px in total. On a 364 px expansion it was the entire
reason it looked broken.

`path_expand()` is a near-linear `lv_bezier3(t, 0, 300, 800, 1024)` and the
same measurement gives 8.8, 10.2, 11.0, 11.0, 11.5, 10.7, 10.4, 9.9, 8.5, 8.0.
Flat, with a soft landing. If you add another large-travel animation, give it
this path rather than a stock one, and check it with `sim --geom`.

### Animations must never carry a fixed start value

`lv_anim_init()` sets `early_apply = 1`, so `lv_anim_start()` applies
`start_value` **immediately**. An animation phrased as "0 → 256 of the journey
from the card to full screen" therefore snaps the view onto the card the
instant it starts, and its inverse snaps it to full screen. Steady state hides
this — the view is already at the start value, so applying it changes nothing.
Interrupt or restart one and you get a single frame of somewhere else before
the animation plays: opening flashed the whole article, and closing flashed a
pale story tint edge to edge, which on the light default theme reads as white.

The fix is structural, not a guard: `rect_cb` interpolates between two
`lv_area_t`s and `detail_move_to()` captures `from` as *wherever the view
currently is* at the moment of starting. `start_value` is then always a no-op
and there is nothing left to teleport to. `det_cur` tracks the geometry rather
than `lv_obj_get_coords()` reading it back, because `lv_obj_set_pos()` defers
to the next layout pass — set a rect and read it back in the same call and you
get the previous one.

The same rule killed the lateral slide for paging: it parked `cont_detail` off
the right edge first, exposing the deck through a story that had not moved.
Paging now animates `translate_x` on `detail_body` alone, so the opaque
full-screen tint never moves and nothing behind it is uncovered.

### The default theme puts hover on a touchscreen

`lv_btn` picks up `styles->pressed` (a darkening colour filter) plus
`transition_delayed` in and `transition_normal` out — a mouse idiom, and it
leaves a highlight sitting on a control after the finger is gone.

Two ways of switching it off do not work:

- `lv_obj_set_style_transition(o, NULL, sel)` is a **null dereference**.
  `lv_obj_set_state()` walks `tr->props` with no NULL check (`lv_obj.c:913`),
  so the object segfaults on its first state change — the first press.
- Overriding it with an empty descriptor does nothing either. That same loop
  gathers transitions from *every* style on the object and keeps the one whose
  selector has the highest state, so the theme's `LV_STATE_PRESSED` entry
  outranks anything set at the default state.

`lv_obj_remove_style_all()` then styling from scratch is what works; see
`make_button()`. Note `LV_THEME_DEFAULT_DARK` is 0, so the base `lv_obj` style
is **white** — anything that ends up with `bg_opa` set and no `bg_color` shows
up as a white block.

**Never make hiding a full-screen container reachable only from an
`lv_anim` ready callback.** A deleted or interrupted animation does not fire
one, and `cont_detail` / `cont_list` are full-screen and `CLICKABLE`. A hide
that gets skipped leaves an invisible surface swallowing every tap, which does
not present as a missed animation — it presents as the entire UI having died,
with a perfectly correct-looking screen. `detail_park()` is the unconditional
put-it-away path and every route out of a story ends there, the animated one
included.

### What the deck could not copy from the widget

The Mac deck flips the outgoing card on its Y axis, scales it to 0.86 and
blurs it. **None of that is available here**, and it is worth writing down so
it is not attempted again:

- LVGL 8 has no 3D rotation and no blur, under any configuration.
- `transform_zoom` / `transform_angle` *do* apply to ordinary objects, but only
  by rendering the object to a full-size intermediate buffer first — and
  transform layers are excluded from the chunked subdivision that keeps simple
  layers small (the `LV_DRAW_LAYER_FLAG_CAN_SUBDIVIDE` branch in `lv_refr.c`'s
  `refr_obj`). A card this size with alpha wants ~130 KB from the pool, the
  layer allocation returns NULL, and the card **silently does not draw at
  all**. Shrinking the card does not rescue it at any useful size.

So depth is built from things the panel can actually draw: each ledge is
really narrower and really lower, and the outgoing card really slides and
fades. The ledges also stay put and only cross-fade their tints, rather than
shifting a slot as they do on the Mac — all you ever see of one is 16 px, the
stack genuinely does not move when a card leaves, and the rising card covers
slot 1 exactly, so it reads as the next ledge lifting off the pile.

The headroom above 66 KB is not slack, either: fading a card during a flip
drops its opacity below 255, which makes LVGL render it through a layer, and
each of the two cards in flight can ask for `LV_LAYER_SIMPLE_BUF_SIZE` (24 KB).
Those degrade gracefully rather than hanging, but only if something is left to
give them.

## Two memory traps that cost a flash cycle each

**1. Do not grow `LV_MEM_SIZE` in internal RAM.** The deck needs ~66 KB where
the old list fit in 48 KB, and the obvious fix — bump the static pool to
128 KB — compiles, links, boots, and draws the entire UI correctly. Then
Wi-Fi fails at runtime, because its RX buffers need internal RAM:

```
E wifi: Expected to init 4 rx buffer, actual is 2
E WiFiGeneric.cpp: esp_wifi_init 0x101: ESP_ERR_NO_MEM
E STA.cpp: begin(): STA enable failed!
[News] Skipping fetch — Wi-Fi not connected
```

A perfect-looking panel that never fetches a story does not read as a memory
problem at all. Link-time RAM went 40.1% → 65.1% and that 80 KB was the
difference.

The pool is therefore in PSRAM (`lv_conf.h`, `LV_MEM_CUSTOM` under
`#ifdef ESP_PLATFORM`). The board has 8 MB and already draws from it; only
widget structs, styles and label text come out of `lv_mem`, while the DMA
buffer is allocated separately with `MALLOC_CAP_DMA` and is untouched. **RAM
is now 25.1%** — better than the 40.1% before the deck, because the old 48 KB
pool left internal RAM as well.

**2. `NEWS_BASE_URL` is a DHCP address and it moves.** After flashing, the
device connected fine and every fetch came back `HTTP -1 / connection
refused` — with `esp-serve` running the whole time. The Mac had simply moved
from `192.168.1.171` to `.187`. Check `ipconfig getifaddr en0` against
`src/news_client.h` before debugging anything else, and reserve the address on
the router if you want this to survive unattended.

### Palette — now shared with the widget

The colours are the Mac widget's, sampled by k-means from the wallpaper: the
amber ramp `#FEE182 #FDD271 #F6B759 #F6AA4B`, the coral end `#E48142 #D6683F
#CA5636`, the blue edges `#9AD7FC #76ACDE #4D7DB3`, and a `#E6EEF3` near-white
for the unknown-area fallback. The shell is that coral end crushed to ~9%
brightness; a neutral charcoal is the obvious choice and is subtly wrong,
because against a screen full of orange it reads blue.

This used to be the pomodoro family — navy shell, coloured badge text — and
both READMEs described the divergence as deliberate. **That is no longer
true**: the device paints whole cards in the tint now, exactly as the widget
does, so it needs colours light enough to take ink text. `AREA_STYLES` here
and `AreaStyle` in `desktop/…/Theme.swift` now agree on keys, labels *and*
colours. Adding an interest area to `interests.yaml` still means adding it in
both places.

Text on a card is `CLR_INK` (`#2E1608`) — never pure black, which on a
saturated orange reads as a hole punched in the card. Secondary text is that
same ink at reduced opacity rather than a second colour, so it stays correct
on every tint. The opacities are lifted from the widget's 0.58 / 0.62 to
0.69 / 0.71: the originals were picked against a 2x Retina panel and came out
looking like disabled text here, partly from 16-bit colour quantising the
blend and partly from Montserrat having no weight to fall back on.

### Type scale

Montserrat ships in one weight only — no bold. Hierarchy comes from size,
colour and spacing. This is the one thing the widget has that the device
cannot copy: there the headline is San Francisco Bold and carries the card by
itself, here it carries it by size and ink alone. Use the named roles, not raw
font references:

```
FONT_META    12   badges, source, score   (letter-spaced when uppercase)
FONT_BODY    14   summary prose           (BODY_LEADING 4)
FONT_TITLE   16   list headlines          (TITLE_LEADING 1)
FONT_DISPLAY 20   detail headline, header
```

**These are our own Montserrat builds, not LVGL's.** `lv_font_montserrat_*` is
ASCII-only, so every accented Spanish word rendered as empty boxes — and since
Phase 9, Spanish articles carry ~800 characters of accented prose instead of a
headline that might happen to dodge it. The LLM also writes typographic quotes,
so U+2019 turned every "Spain's" into a box.

`src/fonts/news_font_{12,14,16,20}.c` are generated by `tools/gen_fonts.sh`
(needs `npm install -g lv_font_conv`). Same face and bpp as LVGL's own build,
same FontAwesome symbol list — drop those and LISTEN / BACK / STOP lose their
icons — over a wider text range: ASCII, Latin-1 Supplement, curly quotes, en/em
dash, ellipsis, euro. **Don't hand-edit the generated files**; change the range
in the script and re-run.

All the built-in `LV_FONT_MONTSERRAT_*` are therefore `0` in `lv_conf.h`, with
`LV_FONT_CUSTOM_DECLARE` and `LV_FONT_DEFAULT` pointing at ours. Adding a size
means editing the script, `lv_conf.h`'s declare line, and nothing else — the
simulator picks up `src/fonts/*.c` by wildcard. Net cost of the switch was
+32 KB of flash, because the unused built-in 36 and 44 went away with it.

Generated fonts need `-DLV_LVGL_H_INCLUDE_SIMPLE` (set in both `platformio.ini`
and `sim/Makefile`) — without it `lv_font_conv`'s output looks for
`lvgl/lvgl.h`, which isn't how the library sits on the include path.

---

## Audio — two things that will waste your time

**1. The codec runs at 16 kHz. Do not change it here.**
`audio_beep_init()` programs the ES8311's internal clock dividers for 16 kHz.
`i2s_set_clk()` re-clocks only the ESP32 side; the codec keeps decoding at
16 kHz regardless. Feeding it 24 kHz produces garbled speech that is completely
unaffected by volume. The **backend** resamples OpenAI's fixed 24 kHz PCM down
to 16 kHz (`esp_news/tts.py`) so the device gets the format it is set up for.

**2. Volume is `NEWS_AUDIO_DAC_VOLUME` (ES8311 register 0x32), not software gain.**
`audio_beep_init()` leaves the DAC at `0xFF` (0 dB, maximum); the beep survives
that only by generating its square wave at amplitude 9000 of 32767. Speech
arrives near full scale, so at `0xFF` the amplifier and the small MX1.25 speaker
are driven flat out — which sounds *muffled as well as loud*, because clipping
smears the high frequencies. Attenuating samples in software does **not** help:
the analog stage still runs at full gain. This was tried and measured; don't
repeat it. Current value `0xC8` (~−27.5 dB), tuned by ear. −0.5 dB per step.

Also inherited from the sibling projects: use the legacy `driver/i2s.h` API.
`driver/i2s_std.h` deadlocks on this platform when the channel idles.

---

## Backend contract

`NEWS_BASE_URL` in `src/news_client.h` is shared by the digest and audio
modules. Empty string = run on built-in sample data with no network.

Since Phase 9, `summary` is an LLM summary of the **fetched article**, not the
RSS blurb — written to an ~800-character target, trimmed by the backend to 900,
and held in a 960-byte buffer. When a page can't be fetched (paywall, 403,
JS-only) the backend falls back to the RSS text rather than inventing one, so
the occasional short entry is expected, not a bug.

```
GET  {base}/digest.json     {"articles":[{title,summary,source,matched_area,score,url}]}
GET  {base}/audio/{i}.pcm   raw PCM: 16 kHz, 16-bit signed LE, mono
POST {base}/refresh         re-run the pipeline; 202 immediately, work in background
GET  {base}/health          {"refreshing": bool, ...} — how the device knows it finished
```

Field names match the Python `Article` model exactly, so the backend needs no
translation layer. Serve it with:

```bash
cd ~/dev/esp-news-reporter && uv run esp-serve --port 8010
```

**Port 8010, not 8000 — Docker holds 8000 on this Mac.** The Mac's LAN address
is compiled into `NEWS_BASE_URL` and is DHCP; reserve it on the router for
unattended use.

---

## The BOOT button (GPIO 0)

On the **detail** view it goes back to whichever view opened the story. On the
**deck** and the **list** it triggers a full manual refresh — this is the
widget's ↻ button, and it means the same thing there. The distinction matters:

| | what it does | how long |
|---|---|---|
| `news_client_request_refresh()` | re-GET `digest.json` | ~100 ms |
| `news_client_request_rebuild()` | `POST /refresh`, wait, then re-GET | 20-30 s |

The backend only rewrites `digest.json` when the pipeline runs — otherwise from
a launchd job at 08:00, or this button. So the cheap re-fetch redraws the *same
stories* and reads as a dead button; the BOOT press does the rebuild instead.

`news_rebuild()` blocks `news_task` while it waits, which is fine — the UI never
calls it, it only sets `rebuild_req` and reads `news_rebuilding` to show
"refreshing..." in the header. The wait polls `/health` every 2 s and gives up
after 150 s, with two escape hatches: if `POST /refresh` fails outright it falls
back to a plain re-fetch (so an older backend still does something useful), and
if `/health` never reports the run as started within 10 s it stops waiting
rather than hanging for the full ceiling.

Measured against the real backend: `POST` returns `202 {"status":"started"}`,
`/health` shows `refreshing: true` within 2 s and flips back to `false` in
~16 s with a warm embedding cache.

In the simulator, press `B` — or headlessly:

```bash
./sim --shot refresh.bmp --boot   # captures the "refreshing..." header state
```

The sim's stub fakes the rebuild on a detached thread for 3 s, so the state is
visible without waiting the real 30.
