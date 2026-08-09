# The desktop widget

The ESP32 reader, as a card deck that sits on the Mac desktop. Same digest,
same backend, same scores — one story at a time, flip through with a button,
open the one you want for the full summary and narration.

This is a **front end, not a second pipeline.** It talks to the FastAPI app in
`src/esp_news/api.py` over the exact contract the firmware uses and knows
nothing about RSS, scoring or LangGraph. Everything in
[the main README](../README.md) about when the feeds actually update applies
here unchanged.

```
┌──────────────────────────────┐
│ NEWS                4h ago ↻ │
│  ┌────────────────────────┐  │   the top card: badge = winning
│  │ [INTERP]          0.56 │  │   interest area, number and bar =
│  │                        │  │   score in the realistic band
│  │ User awareness in      │  │
│  │ frontier models        │  │
│  │ A Translucent/Align…   │  │
│  │ ──────────────────  ↗  │  │   click anywhere: open the story
│  │ Alignment Forum        │  │
│  └────────────────────────┘  │
│   ╰──────────────────────╯   │   the rest of the digest, behind
│    ╰────────────────────╯    │
│  ‹         1 / 10         ›  │
└──────────────────────────────┘
```

## Running it

The backend has to be up, since the widget only reads what the pipeline has
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
| `›` / `‹` | next / previous story — flips the deck |
| Click a card | opens the story |
| `esc` | back to the deck |
| `→` / `←` | flip the deck, or walk stories with one open |
| `space` | open the top card |
| LISTEN | streams `/audio/{i}.pcm` and plays it |
| ↻, or `⌘R` | `POST /refresh` — re-runs the whole pipeline, ~30 s |
| `⌘L` | re-read the digest without re-running anything |
| Menu bar 📰 | show/hide, refresh, placement, open at login, quit |
| Right-click header | refresh, reload, placement, open at login, quit |
| `⌘D` | desktop ⇄ floating |
| `⌘Q` | quit |
| Drag anywhere | move the widget |

The keys only reach a window that can take focus, which in desktop placement
it never does — that is why the deck is driven by on-screen buttons and the
keyboard is a convenience on top rather than the other way round.

The ↻ button is the BOOT button. It matters that it re-runs the pipeline
rather than re-fetching `digest.json`: fetching RSS only happens when the
pipeline runs, so a plain re-fetch would redraw the same stories and look like
the button did nothing.

### Opening a story

The card does not slide aside and the story does not arrive from an edge: the
story *is* the card, opened out. It starts at the tapped card's exact rect, in
its tint and at its corner radius, and grows to fill the panel; closing shrinks
it back into the card it came from. The same transition as the ESP32 panel,
which is where it was worked out — see `firmware/WORKING-NOTES.md`.

Two nested frames do it, and the order is load-bearing. The inner one lays the
story out at the **panel's** size, so the text is wrapped for where it is
going; the outer one is the window that grows, anchored top-left, with the rest
clipped away. Laying the story out at each intermediate size instead would
re-wrap every line on every frame and the headline would visibly reflow the
whole way open. On the firmware this falls out of LVGL clipping children to
their parent's rectangle; in SwiftUI it has to be asked for.

### Being a widget rather than an app

| Behaviour | Notes |
|---|---|
| Sits at the desktop layer | behind every window, pinned across Spaces, no shadow |
| Fades when unused | drops to 55% when the pointer is elsewhere, full on hover. Desktop placement only — floating is an explicit "show me this now" |
| Snaps to screen edges | within 18 pt of an edge it takes a 20 pt margin, so it lines up with the stock widgets. Snaps to `visibleFrame`, so "top" is below the menu bar and "bottom" is above the Dock |
| Opens at login | `SMAppService`, toggled from either menu |
| No Dock icon | `.accessory` policy, so the menu bar item is the only chrome |

**Open at login only works from a bundle.** `make dev` runs a bare executable
with no `Info.plist`, `SMAppService` has nothing to register, and the menu item
is correctly greyed out. Use `make run` or `make install`. The menu reads the
state back from the system every time it opens rather than caching it, because
it can be changed behind the app's back in System Settings → General → Login
Items, and a checkmark that disagrees with reality is worse than none.

## Markdown in summaries

Since Phase 9c the summarizer may emit a two-item subset — `**bold**` for the
few figures worth spotting, and `- ` bullets when the piece really is a list —
with blank-line paragraphs. This panel renders all of it; see
`src/esp_news/markdown.py` for why the subset is that narrow, and note that the
ESP32 cannot render any of it (an LVGL label draws in exactly one font) so it
strips instead.

Parsing is deliberately `.inlineOnlyPreservingWhitespace`. Full Markdown would
read `C#` at the start of a line as a heading and `1.` as an ordered list, and
this is a news digest where both occur in real prose — as do the summaries
already cached under the older prompt, written with no thought for Markdown at
all. Inline-only interprets `**bold**` and leaves every block-level character
alone; bullets are found by a narrower rule in `Paragraphs.swift`. CommonMark
already declines to emphasise intra-word underscores, so `snake_case_names`
survives without special handling.

Card excerpts are flattened back to plain prose: two clipped lines have no room
to be anything else, and a list rendered into them reads as a broken sentence.

## The deck

The list this replaced needed 700 px of screen to be worth reading. A deck
needs the height of one card, which is what got the widget down to 288×300 —
small enough to leave on the desktop rather than something you close.

Pressing `›` softens the top card, swings it on its Y axis, and drops it
behind the stack while the next one rises into its place.

That animation is the reason the deck is **not** indexed into `articles`. With
a plain index, the card you just left stops existing the moment the index
moves, and SwiftUI has nothing to animate *from* — you get a cut, not a flip.
So the deck is indexed by a monotonic `position`, the article shown is
`articles[position mod count]`, and view identity is the position rather than
the article. Rendering the window `position-1 … position+3` then means a step
changes only *properties* of views that already exist:

```
position 3 -> 4
  p=3   slot  0 -> -1     softens, blurs, flips, drops behind
  p=4   slot  1 ->  0     rises into place
  p=7                3    appears at the back, fades in
```

Cycling in both directions falls out of this for free, and a short digest just
repeats. `DeckView.swift` has the slot table.

Two things that look like polish and are not:

- **The drop has to beat the shrinkage.** A card scaled to 0.85 about its
  centre pulls its own bottom edge up ~15 px, so a stack offset of less than
  that hides behind the top card entirely and the deck looks like one card
  with a heavy shadow.
- **Peek cards are veiled in their own tint, not replaced by a blank view.**
  Swapping the view changes its type, which SwiftUI cannot cross-fade, so the
  rising card would pop from empty to full mid-flip. They are also not
  blurred: all you ever see of them is a 15 px ledge, and blur turns three
  ledges into one smear plus a glow where their shadows used to be.

## Colours

Every colour comes out of the wallpaper, sampled by k-means over a screenshot
rather than eyeballed — the amber ramp `#FEE182 #FDD271 #F6B759 #F6AA4B`, the
coral end `#E48142 #D6683F #CA5636`, and the blue edges `#9AD7FC #76ACDE
#4D7DB3`. Each interest area gets one of them as its card tint.

The shell is that coral end crushed to ~9% brightness. A neutral charcoal is
the obvious choice and is subtly wrong: against a screen full of orange it
reads blue.

Change the wallpaper and this stops matching. Re-sampling is a dozen lines of
AppKit over `NSBitmapImageRep.colorAt` — the palette lives in one table in
`Theme.swift` and nothing else knows a hex code.

## Placement

Two modes, because they are different tools. Toggle with `⌘D` or the header's
right-click menu; the choice persists.

**Desktop** (the default) is the widget behaviour. It sits just above the
wallpaper and below every ordinary window. It never comes forward, never
appears in `⌘Tab` or Exposé, stays put while Spaces slide past, and casts no
window shadow.

Specifically it sits at `kCGDesktopIconWindow + 1`, and the `+ 1` is the whole
thing. Finder draws desktop icons in a **full-screen** window at exactly
`kCGDesktopIconWindow`, so a widget at the same level loses the tie twice
over: your folders paint on top of it, and Finder's window swallows every
click in the region before it can reach the widget. The result looks present
and is completely inert — and no amount of fixing event handling on this side
helps, because no event ever arrives. One level up is still astronomically
below `kCGNormalWindowLevel` (0), so every real window stays in front.

**Floating** pins it above everything instead, across Spaces. Useful while you
are actually reading a story; intrusive otherwise.

Neither mode has a Dock icon or app-switcher entry, and position and size
persist across launches.

If desktop mode ever leaves you unable to reach it, the placement is just a
default:

```bash
defaults write com.willtobin.esp-news-widget placement floating
```

### The two traps that eat every click

**It drags around the desktop perfectly and nothing inside it works.** That
one symptom has two independent causes, and fixing either one alone changes
nothing you can see — which makes it look like the fix was wrong.

*First mouse.* An accessory app at the wallpaper layer is never the active
application, so every click on it is a "first mouse" click, and AppKit's
default is to swallow that click to activate the app rather than deliver it.
`ClickThroughHostingView` returns true from `acceptsFirstMouse(for:)`.

*Window dragging.* `NSHostingView.mouseDownCanMoveWindow` is `true`, and a hit
test anywhere in the content returns the hosting view itself — SwiftUI routes
events internally, so there are no per-control subviews to say otherwise. With
`isMovableByWindowBackground` set, AppKit therefore treats **every** mouse-down
in the whole widget as the start of a window drag and consumes it before
SwiftUI sees anything. This is the one that actually kills the buttons.

```
NSHostingView.mouseDownCanMoveWindow  = true    ← both measured
hitTest(centre) -> NSHostingView, canMove=true    with a 20-line harness
```

So `isMovableByWindowBackground` is off, `mouseDownCanMoveWindow` is
overridden to false, and dragging is a `DragGesture` in `RootView` that moves
the window through `PanelController`. `minimumDistance: 6` separates the two:
a click that never travels 6 points is a click, and the card's own tap gesture
wins it because a child's gesture outranks a parent's.

The drag reads `NSEvent.mouseLocation` — screen coordinates — rather than the
gesture's translation. Translation is reported relative to the window, and
this gesture moves the window, so feeding it back makes the widget chase the
cursor across the screen.

### The hidden-title-bar trap

A `.titled` window with `titleVisibility = .hidden`, a transparent title bar
and every standard button hidden still reports `safeAreaInsets.top == 32` to
whatever is inside it. `.fullSizeContentView` does *not* change this — the
content view really does get the whole frame; SwiftUI just lays out below the
inset anyway. Borderless reports 0. Neither is documented; both are a
four-line harness away from being measured.

With an opaque window background nobody notices — the UI starts a little lower
than the frame. Against a transparent window it is a bite out of the top.
`.ignoresSafeArea()` is not the fix: it moves the content up into the strip
AppKit still treats as title bar, where it is not drawn at all, so the header
vanishes instead. The widget is borderless.

### The isFloatingPanel trap

`NSPanel.isFloatingPanel` is a level setter wearing a Bool's clothing —
assigning it rewrites `window.level` (`true` → `.floating`, `false` →
`.normal`). Set it *after* the level and the panel silently drops to layer 0,
which is indistinguishable from the desktop level having been ignored.
`PanelController.apply()` sets it first, deliberately.

Window levels are verifiable without screenshots, which is how that bug was
caught — `CGWindowListCopyWindowInfo` reports the real layer the window server
assigned, and it returns windows front-to-back, so it also answers "who is on
top of whom". Desktop placement should report `-2147483602` and appear
*earlier* in the list than Finder's `-2147483603` full-screen desktop window:

```
front-to-back #59  ESP News   layer=-2147483602   288x300
front-to-back #65  Finder     layer=-2147483603   1470x956
```

Same layer, or listed after Finder, means the widget is buried.

## Snapshots

Same motivation as `firmware/sim/`: looking at the UI should cost seconds.

```bash
swift run ESPNewsWidget --snapshot /tmp/snap             # against live data
swift run ESPNewsWidget --snapshot /tmp/snap --offline   # built-in fixture
```

Writes `deck-0.png`, `deck-1.png`, `deck-2.png` and `detail.png` at the real
panel size and 2x scale, on a slab of the wallpaper's mid-orange so the widget
is judged against something like what it will sit on. Three deck positions
rather than one, because the tints and the peek stack change under you as it
advances and a single frame proves nothing about either.

These render the real views, not copies — `DetailView` takes a `scrollable`
flag that swaps the `ScrollView` for a plain top-aligned stack, because
`ImageRenderer` does not lay out scroll content and would otherwise hand back
an empty rectangle.

**What a snapshot cannot show is the flip**, which is most of the design. For
that, run it.

The live window is also capturable without any screen-recording prompt, which
is worth knowing when a layout bug only appears once the thing is on screen:

```bash
screencapture -x -o -l $(...window id from CGWindowListCopyWindowInfo...) out.png
```

## What is shared with the firmware, and what is not

The interest-area *keys, labels and colours* are shared with `AREA_STYLES` in
`firmware/src/news_ui.cpp`, as is the score band. **Adding an interest area to
`interests.yaml` means adding it in both places.** An unknown area falls back
to a neutral `NEWS` card rather than disappearing.

The colours used to be deliberately unshared — the device painted a small
badge on a dark navy card, this painted the whole card — but the firmware has
since taken this deck wholesale onto the 172×640 panel and paints whole cards
too, so both now run the same wallpaper palette. A tint that fails on one is a
bug on the other.

**The deck itself is shared as a design, not as code.** The device rebuilds it
in LVGL, and two things did not survive the trip: there is no 3D flip and no
blur, because LVGL 8 has neither, and scaling a card is impossible in practice
because LVGL renders transformed objects through a full-size intermediate
buffer it cannot afford. Depth over there is real geometry — narrower, lower
ledges — plus a slide and a fade. `firmware/CLAUDE.md` has the details.

Type weight diverges for the same reason it always did: LVGL ships Montserrat
in one weight, so the device builds hierarchy from size and colour alone,
while the whole San Francisco family is available here. The device also runs
its ink opacities a few points higher, because 16-bit colour quantises the
blend and there is no bold to compensate with.

Audio format is **not** hardcoded — it is read from the `X-Sample-Rate` /
`X-Bits-Per-Sample` / `X-Channels` headers the backend sends. `tts.py`
resamples OpenAI's 24 kHz PCM down to 16 kHz, and hardcoding the wrong one
gets you narration at 1.5x speed that sounds like broken synthesis.

## Why not a real widget

A WidgetKit widget — the kind that lives in Notification Center and on the
desktop — needs a widget extension target, which needs full Xcode, not just
the Command Line Tools. It is also a static snapshot: no deck, no flip, no
audio, a handful of headlines refreshed on a timer the system controls. That
loses the summary and the narration, which are most of the reason the reader
exists.

This keeps the whole reader and builds with the toolchain that is already
here. If the read-only glance version is ever wanted too, it would be an
addition to this package rather than a replacement — `Article`, `NewsClient`
and `Theme` would carry over untouched.
