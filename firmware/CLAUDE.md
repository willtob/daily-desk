# News Display — ESP32-S3-Touch-LCD-3.49

Touch news reader for the 172×640 portrait panel. Fetches a scored, curated
digest over HTTP from the `esp-news-reporter` backend (`~/dev/esp-news-reporter`),
shows it as a scrollable list, and reads articles aloud through the ES8311 codec.

This firmware lives at `~/dev/esp-news-reporter/firmware/` — the same repo as
the Python backend it talks to, since they are one project (the backend's
`esp-news-plan.md` calls this Phase 7).

Board notes: [`../docs/HARDWARE.md`](../docs/HARDWARE.md) (copy of
`~/Desktop/ESP32/esp32-projects/HARDWARE.md`). Same driver layer and
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
./sim --shot out.bmp --scroll 900          # scroll the list down 900 px first
./sim --shot out.bmp --tap 120             # tap at y=120 -> opens that card
./sim --shot out.bmp --tap 120 --swipe right   # open a story, then swipe back
./sim --shot out.bmp --tap 120 --swipe left    # open a story, then next story
```

`--swipe` interpolates the drag over several indev reads, because LVGL needs
movement spread across polls to classify it as a gesture rather than a click.

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
- Objects depend on `lv_conf.h`, because enabling a font otherwise leaves a
  stale empty object and the link fails with an undefined symbol. That
  dependency must be declared **after** the `sim:` rule — a target listed
  earlier becomes make's default goal.
- Requires SDL2: `brew install sdl2`.

---

## Build & flash

```bash
~/.platformio/penv/bin/pio run            # compile
~/.platformio/penv/bin/pio run -t upload  # flash
~/.platformio/penv/bin/pio device monitor # serial @ 115200
```

Size as of the last change: RAM ~32%, flash ~21%.

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

## Project structure

Only these are project code. Everything else is the copied Waveshare driver
layer — **do not rewrite `lvgl_port.c`, `i2c_bsp.c`, or `drv/`**.

```
src/news_ui.cpp/.h        both views, all LVGL widgets, 250 ms refresh
src/news_client.cpp/.h    HTTP + ArduinoJson digest polling
src/news_audio.cpp/.h     narration playback (streams PCM to I2S)
src/audio_beep.cpp/.h     ES8311 + I2S bring-up, copied from PomodoroTimer
src/wifi_manager.cpp/.h   Wi-Fi association
src/main.cpp              init order
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

- All 12 cards are built up front and shown/hidden with `LV_OBJ_FLAG_HIDDEN`.
  Hidden children are skipped by the flex layout, so the list closes up on its
  own and no widget is ever created in the render path.
- `list_body` and `detail_body` **deliberately enable** `LV_OBJ_FLAG_SCROLLABLE`.
  The pomodoro UI clears it everywhere; that is wrong here.
- Titles use `LV_LABEL_LONG_WRAP` with height left at `LV_SIZE_CONTENT`. They
  were clipped to 3 lines once; at 144 px that is ~45 characters and it hid the
  part of the headline that says what the story is.
- Gestures are handled by one callback on the screen, relying on
  `LV_OBJ_FLAG_GESTURE_BUBBLE`. Swipe right = back, swipe left = next story.
  Both bodies scroll vertically, so horizontal is free and LVGL suppresses a
  gesture when the press became a scroll.
- View transitions animate `x` via `lv_anim`; `lv_anim_del` runs before each
  start so a fast double-swipe cannot stack animations.

### Type scale

Montserrat ships in one weight only — no bold. Hierarchy comes from size,
colour and spacing. Use the named roles, not raw font references:

```
FONT_META    12   badges, source, score   (letter-spaced when uppercase)
FONT_BODY    14   summary prose           (BODY_LEADING 4)
FONT_TITLE   16   list headlines          (TITLE_LEADING 1)
FONT_DISPLAY 20   detail headline, header
```

Enabled sizes in `lv_conf.h`: 12, 14, 16, 20, 36, 44. **Not** 18, 22, 24 —
using a disabled size fails at link time. Enabling one requires a rebuild of
everything (see the simulator notes).

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

```
GET {base}/digest.json     {"articles":[{title,summary,source,matched_area,score,url}]}
GET {base}/audio/{i}.pcm   raw PCM: 16 kHz, 16-bit signed LE, mono
```

Field names match the Python `Article` model exactly, so the backend needs no
translation layer. Serve it with:

```bash
cd ~/dev/esp-news-reporter && uv run esp-serve --port 8010
```

**Port 8010, not 8000 — Docker holds 8000 on this Mac.** The Mac's LAN address
is compiled into `NEWS_BASE_URL` and is DHCP; reserve it on the router for
unattended use.
