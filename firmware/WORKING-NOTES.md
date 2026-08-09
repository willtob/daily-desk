# Working notes — how UI work on this board goes wrong

[CLAUDE.md](CLAUDE.md) is the architecture and the LVGL rules. This file is the
other half: the process mistakes that cost real time on this project, written
down because every one of them was made confidently.

The short version: **this panel punishes reasoning that was never checked
against the hardware.** It is slow in ways that are not obvious, its driver is
configured in a way that changes how LVGL behaves, and its failure modes look
like completely different bugs than they are. Measure first.

---

## 1. Do not theorise about performance. Measure it on the board.

Three confident hypotheses about this display, all wrong:

| Believed | Actually |
|---|---|
| The expand animation is too expensive to render | It is the **cheaper** of the two — 40 ms/frame against the deck flip's 70 ms |
| Lowering `LV_DISP_DEF_REFR_PERIOD` will buy frames | Neither animation even reaches the existing 30 ms; the timer was never the limit |
| `full_refresh` means a flip only redraws the card band | `full_refresh = 1` redraws the **entire panel every frame**, always |

That third one was written into the docs as justification for a change that
then froze the display. A plausible-sounding reason in a comment is not
evidence, and it outlives the person who wrote it.

**How to measure.** Count `lv_anim` exec callbacks against `millis()` and print
in the ready callback:

```c
if (frames == 0) t0 = millis();
frames++;
/* ... in the ready_cb ... */
Serial.printf("[x] %u frames in %u ms\n", frames, millis() - t0);
```

Auto-fire it from `ui_timer_cb` a few seconds after the digest lands so the
numbers come off the board with nobody standing at it. Space the self-test
wider than the animation, or it interrupts itself and never prints.

The output of that, for reference — the panel runs animations at **14–27 fps**,
and the ceiling is render time plus a 220 KB full-panel blit. The only lever is
**distance travelled per frame**, i.e. duration and easing. See the frame
budget table in CLAUDE.md before touching any timing.

## 2. Keep the simulator honest, or it certifies the wrong thing

The sim compiles the real `news_ui.cpp`, which makes it easy to assume it
proves things. It only proves what it actually replicates.

It spent this project using a single 172×80 partial buffer while the board uses
**two full-screen buffers with `full_refresh = 1`**. Same pixels, different code
path: partial mode redraws invalidated rectangles, full refresh throws that away
and redraws everything, then swaps buffers. Ordering bugs around
hide/move/invalidate can exist on the board and not in the sim. It now matches
`lvgl_port.c` — **if the driver's buffer setup changes, change the sim too.**

The touch path is still faked. `inject_tap` is a clean press and release; a
finger is not. Input-timing bugs will still not reproduce here.

## 3. Verify by assertion, not by looking at screenshots

Two ways eyeballing gave confident wrong answers in one session:

- **Scanning pixels to measure the transition** found the deck's header rule and
  ledges — they sit at exactly the edges being measured, and the deck behind is
  the same tint as the card. Use `--geom`, which asks the object for its
  coordinates.
- **`--settle` equal to the animation length** screenshots one refresh early.
  The result is a nearly-arrived story with a few pixels of deck around it,
  which looks exactly like a layout bug and is not one. Settle past the
  *longest* animation.

Prefer a script that prints `ok`/`FAIL` per path over a folder of images. The
15-path suite in the README takes seconds and has caught a segfault, a swallowed
tap and two wrong end-states.

`--film PREFIX` dumps every frame. A one-frame flicker cannot be caught any
other way, because `--settle` cannot reach inside a tap.

## 4. One behavioural change per flash

Three changes went into one build: refresh period, expand-to-fill, and removing
the list header. It froze. The freeze was fixed; then clicks stopped working
entirely, and there was no way to tell which of the three was responsible.

The whole lot got reverted — including work that had been asked for and was
fine. A flash cycle is ~25 s. Bundling saved a minute and cost an afternoon.

**Before reverting, preserve.** `git diff -- firmware > wip.patch` plus full
copies of the touched files into the scratchpad. That is why nothing was
actually lost.

## 5. The recurring bug class here: state restored on only some paths

Every serious breakage in this UI has been one shape — something set on entry
whose reset lives on a path that does not always run.

- Hiding a full-screen `CLICKABLE` container from an `lv_anim` ready callback.
  A deleted animation never fires one, so the hide is skipped and an invisible
  surface eats every touch.
- An animation carrying a fixed `start_value`. `early_apply` is on by default,
  so restarting one teleports the view to that value for a frame.

Neither presents as "the animation looks wrong". The first presents as *the
whole UI is dead*, the second as *it flashes something unrelated*. When a
symptom is that broad, look for a reset that did not run — do not start by
suspecting the renderer.

The fix that works is structural: make the correct state unconditional
(`detail_park()`), or remove the possibility (interpolate from wherever the
object currently is, so there is no start value to snap to). A guard on the
callback path is not the same thing.

## 6. Read LVGL's source rather than recalling its API

LVGL 8 has sharp edges that are not in the docs and not guessable:

- `lv_obj_set_style_transition(o, NULL, sel)` reads like "no transition" and is
  a **null dereference** — `lv_obj_set_state()` walks `tr->props` unchecked
  (`lv_obj.c:913`). It crashes on the object's first press.
- Overriding a theme transition locally does nothing; that loop keeps the
  entry from the highest-state selector, so the theme's PRESSED wins.
- `LV_THEME_DEFAULT_DARK` is 0, so the base `lv_obj` style is **white**.
  Anything with `bg_opa` set and no `bg_color` renders as a white block.

The source is on disk at
`~/Desktop/ESP32/ESP32-S3-Touch-LCD-3.49/Arduino_Libraries/lvgl8/lvgl/src/`.
Checking `lv_refr.c` and `lv_obj_style.c` took two minutes each and both times
overturned what seemed obvious.

## 7. Environment facts that waste time when rediscovered

- `~/.platformio/penv/bin/pio` — not on `PATH`.
- **pyserial lives only in the PlatformIO venv.** `python3 readser.py` fails
  with `ModuleNotFoundError`; use `~/.platformio/penv/bin/python`.
- The serial reader needs a DTR/RTS pulse to force a clean reset, or boot
  output is missed.
- **Piping serial through `grep` can race the buffer** and silently drop the
  line you are waiting for. Dump raw, then filter.
- `NEWS_BASE_URL` is a DHCP address and it moves. Check
  `ipconfig getifaddr en0` against `src/news_client.h` before debugging a fetch
  failure. The live backend is a good source of real test data:
  `curl $NEWS_BASE_URL/digest.json`.
- Never set `upload_port` — a glob reaches esptool verbatim and breaks uploads.
- `~/Desktop` is iCloud-synced and has eaten the LVGL tree once. Deleted files
  go to `~/Library/Mobile Documents/.Trash`, not `~/.Trash`.
- `wifi_credentials.h` is gitignored. Do not inline credentials into
  `wifi_manager.cpp` to "simplify" anything, and check what a verification
  command will print before running it against a secrets file.

## 8. Testing text transforms

`paragraphize()` is worth copying as a pattern. It was verified by extracting
the real function out of `news_ui.cpp` into a generated C harness, running it
against **all ten summaries from the live backend** plus hand-written traps
(`U.S.`, `J. Doe`, `8.000 hectáreas`, curly quotes, accented capitals, empty
string), and asserting that no non-space byte is lost or invented.

The sim's sample articles top out at 235 characters and cannot exercise a rule
tuned for 800. Sample data proves the code runs; real data proves it is right.
