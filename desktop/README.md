# The desktop panel

The ESP32 reader, as a floating panel on the Mac. Same digest, same backend,
same two-view reader — a scrollable list of scored cards, tap through to the
full summary, and narration read aloud.

This is a **front end, not a second pipeline.** It talks to the FastAPI app in
`src/esp_news/api.py` over the exact contract the firmware uses and knows
nothing about RSS, scoring or LangGraph. Everything in
[the main README](../README.md) about when the feeds actually update applies
here unchanged.

```
┌──────────────────┐
│ NEWS   10 stories│   list    scrollable cards, best-scoring first
├──────────────────┤          badge = winning interest area
│ ┌──────────────┐ │          bar   = score within the realistic band
│ │ OPEN SRC 0.64│ │
│ │ Open-Weights │ │   detail full title, source, score, summary,
│ │ Mythos Capa… │ │          LISTEN, and a link to the article
│ │ ──────────── │ │
│ │ LessWrong    │ │
│ └──────────────┘ │
└──────────────────┘
```

## Running it

The backend has to be up, since the panel only reads what the pipeline has
already written:

```bash
uv run esp-serve --port 8010      # from the repo root
```

Then:

```bash
cd desktop
make run        # build ESP News.app and launch it
make install    # copy it to /Applications
make dev        # swift run, for a faster edit loop
```

`make` needs only the Command Line Tools — there is no Xcode project. See
[Why not a real widget](#why-not-a-real-widget).

## Pointing it at the backend

Defaults to `http://127.0.0.1:8010`. Port 8010 rather than 8000 because Docker
holds 8000 on this machine, which is why `esp-serve` is run with `--port 8010`.

```bash
ESP_NEWS_BASE_URL=http://192.168.1.171:8010 make dev

# or persistently, for the installed bundle:
defaults write com.willtobin.esp-news-widget baseURL http://127.0.0.1:8010
```

## Using it

| Action | What happens |
|---|---|
| Click a card | opens the story |
| `esc`, or BACK | back to the list |
| `←` / `→` | previous / next story — the device's swipes |
| LISTEN | streams `/audio/{i}.pcm` and plays it |
| ↻, or `⌘R` | `POST /refresh` — re-runs the whole pipeline, ~30 s |
| `⌘L` | re-read the digest without re-running anything |
| Right-click header | refresh, reload, placement, quit |
| `⌘D` | desktop ⇄ floating |
| `⌘Q` | quit |
| Drag anywhere | move the panel |

The ↻ button is the BOOT button. It matters that it re-runs the pipeline rather
than re-fetching `digest.json`: fetching RSS only happens when the pipeline
runs, so a plain re-fetch would redraw the same stories and look like the
button did nothing.

## Placement

Two modes, because they are different tools. Toggle with `⌘D` or the header's
right-click menu; the choice persists.

**Desktop** (the default) is the widget behaviour. The panel sits at the
wallpaper layer — above the wallpaper, below every ordinary window, exactly
where Finder draws desktop icons. It never comes forward, never appears in
`⌘Tab` or Exposé, stays put while Spaces slide past, and casts no window
shadow. You see it when your desktop is visible and it stays out of the way
the rest of the time.

**Floating** pins it above everything instead, across Spaces. Useful while
you are actually reading a story; intrusive otherwise.

Neither mode has a Dock icon or app-switcher entry, and position and size
persist across launches.

If desktop mode ever leaves you unable to reach the panel, the placement is
just a default:

```bash
defaults write com.willtobin.esp-news-widget placement floating
```

### The first-mouse trap

An accessory app at the wallpaper layer is never the active application, so
*every* click on it is a "first mouse" click — and AppKit's default is to
swallow that click to activate the app rather than deliver it to the view.
The panel's content view is a `ClickThroughHostingView` that returns true from
`acceptsFirstMouse(for:)` for exactly this reason.

The symptom is worth recognising, because it misdirects: dragging the panel
keeps working while every button and card inside is dead, so it reads as "the
content froze after I dragged it". Nothing froze — window-background dragging
is handled by AppKit at the window level and needs no activation, so it was
the one thing that had ever worked.

### The isFloatingPanel trap

`NSPanel.isFloatingPanel` is a level setter wearing a Bool's clothing —
assigning it rewrites `window.level` (`true` → `.floating`, `false` →
`.normal`). Set it *after* the level and the panel silently drops to layer 0,
which is indistinguishable from the desktop level having been ignored.
`PanelController.apply()` sets it first, deliberately.

Window levels are verifiable without screenshots, which is how that bug was
caught — `CGWindowListCopyWindowInfo` reports the real layer the window server
assigned. Desktop placement should report the same layer as Finder's wallpaper
window, currently `-2147483603`.

## Snapshots

Same motivation as `firmware/sim/`: looking at the UI should cost seconds.

```bash
swift run ESPNewsWidget --snapshot /tmp/snap             # against live data
swift run ESPNewsWidget --snapshot /tmp/snap --offline   # built-in fixture
```

Writes `list.png` and `detail.png` at the panel's width and 2x scale. These
render the real views, not copies — `ListView`/`DetailView` take a
`scrollable` flag that swaps the `ScrollView` for a plain top-aligned stack,
because `ImageRenderer` does not lay out scroll content and would otherwise
hand back an empty rectangle.

## What is shared with the firmware, and what is not

The palette, the interest-area badge table and the score band are ported from
`firmware/src/news_ui.cpp` so the two readers look like the same product.
**Adding an interest area to `interests.yaml` means adding it in both places**
— `AREA_STYLES` there, `AreaStyle.table` in `Theme.swift`. An unknown area
falls back to a neutral `NEWS` badge rather than disappearing.

The deliberate divergence is type weight. LVGL ships Montserrat in one weight,
so the device builds hierarchy from size, colour and spacing alone. Here the
whole San Francisco family is available, so headlines carry real weight and
the sizes sit closer together.

Audio format is **not** hardcoded — it is read from the `X-Sample-Rate` /
`X-Bits-Per-Sample` / `X-Channels` headers the backend sends. `tts.py`
resamples OpenAI's 24 kHz PCM down to 16 kHz, and hardcoding the wrong one
gets you narration at 1.5x speed that sounds like broken synthesis.

## Why not a real widget

A WidgetKit widget — the kind that lives in Notification Center and on the
desktop — needs a widget extension target, which needs full Xcode, not just
the Command Line Tools. It is also a static snapshot: no scrolling, no audio,
a handful of headlines refreshed on a timer the system controls. That loses
the summary and the narration, which are most of the reason the reader exists.

A floating panel keeps the whole reader and builds with the toolchain that is
already here. If the read-only glance version is ever wanted too, it would be
an addition to this package rather than a replacement.
