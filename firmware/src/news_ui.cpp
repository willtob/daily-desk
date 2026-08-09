/*
 * news_ui.cpp — the desktop widget's card deck, on the 172 x 640 portrait panel.
 *
 *   DECK view    one story at a time, full bleed in its interest area's colour,
 *                with the rest of the digest stacked behind it as coloured
 *                ledges. Nav bar flips; tap the card to open it.
 *   LIST view    the old scrollable column, restyled to match. Swipe up from
 *                the deck to scan all ten at once; swipe right to go back.
 *   DETAIL view  full title, source, score, scrollable summary and LISTEN,
 *                edge to edge in the same colour as the card it came from.
 *
 * Ported from desktop/Sources/ESPNewsWidget/{DeckView,DetailView,Theme}.swift.
 * The palette, the score band and the interest-area labels are that widget's;
 * see the notes on AREA_STYLES and the deck geometry for the two places the
 * port deliberately diverges, both forced by what LVGL 8 can actually draw.
 *
 * Everything is built once in news_ui_create() and swapped by toggling
 * LV_OBJ_FLAG_HIDDEN, so no widget is ever created outside the LVGL lock.
 * All refreshes happen in ui_timer_cb, which LVGL calls under the lock.
 *
 * Note the deliberate difference from the pomodoro UI: that one clears
 * LV_OBJ_FLAG_SCROLLABLE everywhere. Here the list body and the detail body
 * must scroll, so scrolling is explicitly enabled on those two containers.
 */
#include <Arduino.h>
#include "news_ui.h"
#include "news_client.h"
#include "news_audio.h"
#include "wifi_manager.h"
#ifdef ESP_PLATFORM
#include "imu_wake.h"
#endif
#include "lvgl.h"
#include "fonts/news_fonts.h"
#include "user_config.h"
#include <stdio.h>
#include <string.h>

/* ── Palette — the widget's, sampled from the wallpaper ───────────────
 *
 * These used to be the pomodoro family (navy shell, coloured badge text) and
 * were documented as deliberately *not* shared with the Mac widget. That split
 * is gone: the device now paints whole cards in the interest-area tint the way
 * the widget does, so it needs the widget's colours to do it.
 *
 * The shell is #CA5636 — the wallpaper's deepest red — taken down to ~9%
 * brightness. A neutral charcoal is the obvious choice and is subtly wrong:
 * next to a screen full of orange it reads blue. */
#define CLR_BG        lv_color_hex(0x241713)
#define CLR_SURFACE   lv_color_hex(0x33201A)
#define CLR_RULE      lv_color_hex(0x4A2E24)
#define CLR_DIM       lv_color_hex(0xB89486)
#define CLR_WHITE     lv_color_hex(0xFFF3E6)
#define CLR_ACCENT    lv_color_hex(0xF6AA4B)

/* What goes *on* a card. Never pure black: on a saturated orange, #000 reads
 * as a hole punched in the card rather than as text. Secondary text is this
 * colour at reduced opacity rather than a second colour, so it stays correct
 * on every tint from #FEE182 to #D6683F. */
#define CLR_INK       lv_color_hex(0x2E1608)

/* The widget's 0.58 / 0.62, lifted. Those were picked against a 2x Retina
 * panel; here the same values on the same tints came out washed enough that
 * the excerpt read as disabled text, which is partly the 16-bit colour depth
 * quantising the blend and partly Montserrat having no weight to fall back
 * on. Bumped until the excerpt reads as secondary rather than as greyed out. */
#define INK_SOFT      214   /* 0.84 — summary excerpt on a card  */
#define INK_SOURCE    180   /* 0.71 — provenance                 */
#define INK_BODY      255   /* 1.00 — summary prose in detail    */
#define INK_BAR_TRACK  41   /* 0.16 — the score bar's groove     */
#define INK_BAR_FILL  140   /* 0.55 — the score bar itself       */
#define INK_HAIRLINE   64   /* 0.25 — the rule in detail         */

/* The body was 0.82 and the card excerpt 0.69, and both were too light to
 * read here. The reasoning behind the earlier numbers still holds — they came
 * from the Mac widget, where the same values look right — but three things
 * stack up against them on this panel and not on a Retina display: 16-bit
 * colour quantises the blend, Montserrat has no bold to fall back on so weight
 * has to come from ink alone, and a 172 px column viewed end-on is a harder
 * read than a wide one. The prose is the content; it now gets full-strength
 * ink and hierarchy comes from size and spacing instead. Only genuinely
 * secondary things — the source line, the score groove — stay knocked back. */

/* ── Type scale ───────────────────────────────────────────────────────
 *
 * Three roles, not five sizes used at random. LVGL ships Montserrat in one
 * weight only, so hierarchy has to come from size, colour and spacing — there
 * is no bold to fall back on. This is the one thing the widget has that the
 * device cannot copy: over there the headline is San Francisco Bold and
 * carries the card by itself, here it carries it by size and ink alone.
 *
 *   META  12  badges, source, score. Letter-spaced when it's a label, which
 *             is what makes small uppercase text read as a tag rather than as
 *             shouted body text. The widget sets this at 9.5 pt; 12 is the
 *             smallest generated face, and at 172 px wide it is already small.
 *   BODY  14  summary prose.  TITLE 16  card and list headlines.
 *   DISPLAY 20  detail headline and the header.
 *
 * These are our own Montserrat builds, not LVGL's lv_font_montserrat_*, which
 * are ASCII-only and rendered every accented Spanish word as boxes. Same face,
 * same sizes, wider range — see fonts/news_fonts.h. */
#define FONT_META     &news_font_12
#define FONT_BODY     &news_font_14
#define FONT_TITLE    &news_font_16
#define FONT_DISPLAY  &news_font_20

#define META_TRACKING     1   /* letter-space for uppercase labels */
#define TITLE_LEADING     1   /* headlines run long; default is airy */
#define BODY_LEADING      6   /* prose wants the opposite */
#define CARD_BODY_LEADING 2   /* the card excerpt is tighter than the detail */

/* ── Layout ───────────────────────────────────────────────────────── */
#define PAD          6
#define HEADER_H     44
#define NAV_H        48
#define BODY_W       (EXAMPLE_LCD_H_RES - 2 * PAD)   /* 160 px */
#define CARD_PAD     10
#define TEXT_W       (BODY_W - 2 * CARD_PAD)         /* 140 px */
#define BAR_H        4

/* Corners are generous on purpose — on the widget they are what separates
 * "widget" from "window". Scaled down from the widget's 18 px, which was
 * chosen against a 264 px card and is proportionally huge on a 160 px one. */
#define CARD_RADIUS  14

/* ── The deck ─────────────────────────────────────────────────────────
 *
 * The widget's deck flips the outgoing card on its Y axis, scales it to 0.86
 * and blurs it. **None of that is available here**, and not for want of
 * trying: LVGL 8.4 does apply transform_zoom / transform_angle to ordinary
 * objects, but only by rendering the object to a full-size intermediate
 * buffer first — and transform layers are explicitly excluded from the
 * chunked subdivision that keeps simple layers small (see the
 * LV_DRAW_LAYER_FLAG_CAN_SUBDIVIDE branch in lv_refr.c's refr_obj). A card
 * this size with alpha wants ~130 KB out of a 48 KB LV_MEM_SIZE pool, so the
 * layer allocation fails and the card silently does not draw at all. There is
 * no 3D rotation and no blur in LVGL 8 under any configuration.
 *
 * So depth is built out of things the panel can actually draw: each card
 * behind the top one is really narrower and really lower, and the outgoing
 * card really slides and fades. Same reading, no transforms.
 *
 * The other simplification is the peek stack. On the widget the ledges shift
 * up a slot on every step, because SwiftUI re-slots the views; here they stay
 * put and only their colours cross-fade. All you ever see of a ledge is
 * DROP_STEP pixels, the stack genuinely does not move when one card leaves,
 * and the rising card covers slot 1 exactly — so it reads as the next ledge
 * lifting off the pile, which is what it is. */
#define PEEK          3     /* ledges behind the top card */
#define DROP_STEP    16     /* how far each ledge hangs below the one above */
#define SHRINK_STEP  10     /* and how much narrower it is */

#define CARD_H      276
#define DECK_AREA_H (EXAMPLE_LCD_V_RES - HEADER_H - 1 - NAV_H)
/* Cards are children of cont_deck, which is the whole screen, so this is in
 * screen coordinates — the header and its hairline have to be added back in.
 * Leaving them out centres the deck in the panel instead of in the space
 * between the header and the nav bar, which reads as a stack that has slipped
 * upwards and left a hole under it. */
#define DECK_TOP    (HEADER_H + 1 + (DECK_AREA_H - (CARD_H + PEEK * DROP_STEP)) / 2)

#define FLIP_MS     280

/* Cosine scores from the fitness function realistically land in this band;
 * the score bar maps it to 0..width so differences are actually visible. A
 * pure 0..1 mapping renders every story as half a bar. Shared with the
 * widget's Theme.scoreFraction and the Python score node. */
#define SCORE_MIN    0.25f
#define SCORE_MAX    0.60f

/* ── Views ────────────────────────────────────────────────────────── */
typedef enum { VIEW_DECK, VIEW_LIST, VIEW_DETAIL } view_t;

static view_t view        = VIEW_DECK;
static view_t detail_from = VIEW_DECK;   /* where BACK returns to */

/* ── Widget handles ───────────────────────────────────────────────── */
static lv_obj_t *lbl_status;

/* Deck */
static lv_obj_t *cont_deck;
static lv_obj_t *deck_peek[PEEK];
static lv_obj_t *lbl_deck_empty;
static lv_obj_t *lbl_counter;
static lv_obj_t *btn_prev, *btn_next;

/* Two full card faces, ping-ponged: one leaves while the other arrives. One
 * face cannot animate a step, because the card you are leaving and the card
 * you are arriving at are on screen at the same time. */
static lv_obj_t *face[2];
static lv_obj_t *f_badge[2], *f_score[2], *f_title[2];
static lv_obj_t *f_summary[2], *f_bar[2], *f_source[2], *f_open[2];
static int       face_active = 0;

/* List */
static lv_obj_t *cont_list;
static lv_obj_t *list_body;
static lv_obj_t *card[NEWS_MAX_ARTICLES];
static lv_obj_t *card_badge[NEWS_MAX_ARTICLES];
static lv_obj_t *card_score[NEWS_MAX_ARTICLES];
static lv_obj_t *card_title[NEWS_MAX_ARTICLES];
static lv_obj_t *card_source[NEWS_MAX_ARTICLES];
static lv_obj_t *card_bar[NEWS_MAX_ARTICLES];

/* Detail */
static lv_obj_t *cont_detail;
static lv_obj_t *detail_body;
static lv_obj_t *d_topbar;
static lv_obj_t *btn_back, *lbl_back;
static lv_obj_t *lbl_d_badge, *lbl_d_score;
static lv_obj_t *lbl_d_title, *lbl_d_source, *d_rule, *lbl_d_summary;
static lv_obj_t *btn_listen, *lbl_listen;

/* ── View state ───────────────────────────────────────────────────── */
static int      selected     = -1;   /* article open in DETAIL */
static uint32_t last_version = 0;

/* Monotonic, not an index into news_articles — see step_deck(). May go
 * negative; deck_index() wraps it. Keeping identity on the position rather
 * than the article is what lets the deck cycle in both directions for free
 * and a short digest simply repeat. */
static int deck_pos = 0;

/* ── Interest areas → tint + short badge text ─────────────────────────
 * The keys must match `matched_area` from the Python score node, and the
 * labels are shared with AreaStyle in desktop/Sources/ESPNewsWidget/Theme.swift
 * — adding an area to interests.yaml means adding it in both places.
 *
 * The colours are now shared too. They are the wallpaper's amber ramp, coral
 * end and blue edges, and since the card is painted in them rather than just
 * a badge, they have to be light enough to take ink text. */
typedef struct {
    const char *area;
    const char *label;
    uint32_t    color;
} area_style_t;

static const area_style_t AREA_STYLES[] = {
    { "ai_open_source",     "OPEN SRC", 0xFEE182 },
    { "ai_consciousness",   "INTERP",   0xF6B759 },
    { "classic_ml_applied", "CLASSIC",  0x9AD7FC },
    { "big_tech_career",    "BIG TECH", 0x76ACDE },
    { "embedded_wearables", "EMBEDDED", 0xF6AA4B },
    { "startup_vc",         "STARTUP",  0xFDD271 },
    { "florida",            "FLORIDA",  0xE48142 },
    { "spain",              "SPAIN",    0xD6683F },
};
#define AREA_STYLE_COUNT (sizeof(AREA_STYLES) / sizeof(AREA_STYLES[0]))

/* The wallpaper's near-white. An unknown area gets a neutral card rather than
 * vanishing, so a new area in interests.yaml shows up as un-styled instead of
 * as a blank. */
static const area_style_t AREA_FALLBACK = { "", "NEWS", 0xE6EEF3 };

static const area_style_t *area_style(const char *area)
{
    if (area) {
        for (unsigned i = 0; i < AREA_STYLE_COUNT; i++) {
            if (strcmp(area, AREA_STYLES[i].area) == 0) return &AREA_STYLES[i];
        }
    }
    return &AREA_FALLBACK;
}

/* Score → bar width in pixels, clamped to the visible band. */
static lv_coord_t score_bar_w(float score, lv_coord_t full)
{
    float t = (score - SCORE_MIN) / (SCORE_MAX - SCORE_MIN);
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    lv_coord_t w = (lv_coord_t)(t * full);
    return w < 3 ? 3 : w;   /* always a sliver, so low reads as low not missing */
}

/* ── The Markdown subset, taken back out ──────────────────────────────
 *
 * Since Phase 9c the summarizer may emit `**bold**` and `- ` bullets, and
 * separates paragraphs with a blank line (see src/esp_news/markdown.py). The
 * Mac panel renders all of it. This one cannot, and not for want of effort:
 * **an LVGL 8 label draws in exactly one font**, so bold inside a paragraph
 * would mean splitting every paragraph into separate label objects and
 * positioning them by hand — on a display where the widget pool is already
 * half spent and every frame is a whole-panel blit.
 *
 * So the emphasis is dropped and the text is kept. Doing nothing is not an
 * option: the asterisks would simply be drawn, which is worse than losing the
 * bold. Bullets do survive, as U+2022 — it is in the generated font range
 * (see tools/gen_fonts.sh), and a marker reads as a list even without the
 * hanging indent LVGL cannot give it.
 *
 * If emphasis is ever wanted here, lv_label_set_recolor() and `#RRGGBB text#`
 * is the only inline styling LVGL 8 has; the difficulty is a colour that stays
 * legible on all nine card tints, not the parsing. */
static void md_strip(const char *src, char *dst, size_t cap, bool keep_bullets)
{
    size_t o = 0;
    bool   line_start = true;

    for (size_t i = 0; src[i] && o + 6 < cap; i++) {
        /* `**` in either direction: emphasis is not representable, so both
         * halves go and the words between them stay. */
        if (src[i] == '*' && src[i + 1] == '*') { i++; continue; }

        if (line_start) {
            /* Indentation before a marker is not content. */
            if (src[i] == ' ' || src[i] == '\t') continue;

            if ((src[i] == '-' || src[i] == '*' || src[i] == '+') && src[i + 1] == ' ') {
                if (keep_bullets) {
                    dst[o++] = (char)0xE2;   /* U+2022 BULLET, in UTF-8 */
                    dst[o++] = (char)0x80;
                    dst[o++] = (char)0xA2;
                    dst[o++] = ' ';
                }
                i++;                          /* and the space after it */
                line_start = false;
                continue;
            }
        }

        line_start = (src[i] == '\n');
        dst[o++] = src[i];
    }
    dst[o] = '\0';
}

/* The card excerpt is two clipped lines of a much longer summary, so it wants
 * one run of prose: no markers, and no line breaks turning a list into
 * fragments that read as a broken sentence. */
static void md_plain(const char *src, char *dst, size_t cap)
{
    md_strip(src, dst, cap, false);

    size_t o = 0;
    bool   gap = false;
    for (size_t i = 0; dst[i]; i++) {
        char c = (dst[i] == '\n' || dst[i] == '\t') ? ' ' : dst[i];
        if (c == ' ') { gap = true; continue; }
        if (gap && o) dst[o++] = ' ';
        gap = false;
        dst[o++] = c;
    }
    dst[o] = '\0';
}

/* ── Paragraphing the summary ─────────────────────────────────────────
 *
 * The backend writes one ~800-character block with no line breaks in it. At
 * 14 pt in a 140 px column that is forty-odd unbroken lines, and on a screen
 * this narrow and this tall it reads as a wall: there is no way to find your
 * place again after looking away. The breaks are inserted here, at display
 * time, so news_articles keeps exactly what the backend sent.
 *
 * The rule is deliberately conservative, because a missed break costs nothing
 * and a break in the middle of a name is glaring. Split only on a stop that is
 * followed by a space and a capital; only once MIN_PARA characters have gone
 * by, so a run of short sentences does not shatter into fragments; and never
 * after a single capital letter, which is what "U.S. Steel" and "J. Doe" look
 * like to a sentence splitter. Closing quotes are stepped over, including the
 * multi-byte curly ones these summaries actually use, and a decimal point
 * ("8.000 hectáreas") never splits because no space follows it.
 *
 * Characters rather than sentences are what pace this. The digest runs to
 * 200-character sentences in Spanish and 60 in English, so a sentence-counting
 * rule gives one unbroken slab in the first language and confetti in the
 * second. */
#define MIN_PARA  170

static bool sentence_break(const char *p, const char *start, int *adv)
{
    const char *q;

    if (*p != '.' && *p != '!' && *p != '?') return false;

    q = p + 1;
    if (*q == '"' || *q == '\'') q++;
    else if ((unsigned char)q[0] == 0xE2 && (unsigned char)q[1] == 0x80 &&
             ((unsigned char)q[2] == 0x9D || (unsigned char)q[2] == 0x99)) q += 3;

    if (*q != ' ') return false;

    /* Next word has to look like the start of a sentence. Non-ASCII passes:
     * an accented capital opening a Spanish headline is still a sentence. */
    unsigned char n = (unsigned char)q[1];
    if (n < 0x80 && !(n >= 'A' && n <= 'Z')) return false;

    /* An initial, not a full stop. */
    if (*p == '.' && p > start && p[-1] >= 'A' && p[-1] <= 'Z' &&
        (p - 1 == start || p[-2] == ' ' || p[-2] == '.')) return false;

    *adv = (int)(q - p);
    return true;
}

static void paragraphize(const char *src, char *dst, size_t cap)
{
    size_t o = 0, run = 0;
    int    adv = 0;

    for (size_t i = 0; src[i] && o + 4 < cap; i++) {
        dst[o++] = src[i];
        run++;

        if (run < MIN_PARA) continue;
        if (!sentence_break(&src[i], src, &adv)) continue;

        for (int k = 1; k < adv && o + 4 < cap; k++) { dst[o++] = src[i + k]; }
        i += adv;                 /* land on the space; the loop step eats it */
        dst[o++] = '\n';
        dst[o++] = '\n';
        run = 0;
    }
    dst[o] = '\0';
}

/* Positive modulo. C's % keeps the sign of the dividend, so a plain p % n
 * sends you off the front of the array as soon as you page backwards past the
 * first story. */
static int deck_index(int p)
{
    int n = news_count;
    if (n <= 0) return 0;
    return ((p % n) + n) % n;
}

/* ── Slot geometry ────────────────────────────────────────────────────
 *
 * Slot 0 is the card you are reading, 1..PEEK are the ledges behind it, and
 * SLOT_GONE is where a card goes when it leaves. Everything the deck animates
 * is a lerp between two of these. */
typedef struct {
    lv_coord_t x, y, w;
    lv_opa_t   opa;
} slot_t;

#define SLOT_GONE  (-1)

static slot_t slot_geom(int slot)
{
    slot_t g;
    if (slot == SLOT_GONE) {
        /* Off to the left and slightly up — the direction the widget's card
         * swings before it drops behind the deck. */
        g.x   = PAD - 74;
        g.y   = DECK_TOP - 12;
        g.w   = BODY_W;
        g.opa = LV_OPA_TRANSP;
        return g;
    }
    if (slot < 0)     slot = 0;
    if (slot > PEEK)  slot = PEEK;

    g.w   = BODY_W - slot * SHRINK_STEP;
    g.x   = PAD + slot * SHRINK_STEP / 2;    /* keeps the stack centred */
    g.y   = DECK_TOP + slot * DROP_STEP;
    switch (slot) {
        case 0:  g.opa = 255; break;
        case 1:  g.opa = 235; break;
        case 2:  g.opa = 215; break;
        default: g.opa = 190; break;
    }
    return g;
}

static lv_coord_t lerp(lv_coord_t a, lv_coord_t b, int32_t t)
{
    return (lv_coord_t)(a + ((int32_t)(b - a) * t) / 256);
}

static void apply_slot(lv_obj_t *o, slot_t from, slot_t to, int32_t t)
{
    lv_obj_set_pos(o, lerp(from.x, to.x, t), lerp(from.y, to.y, t));
    lv_obj_set_width(o, lerp(from.w, to.w, t));
    lv_obj_set_style_opa(o, (lv_opa_t)lerp(from.opa, to.opa, t), LV_STATE_DEFAULT);
}

/* ── Filling a card face ──────────────────────────────────────────── */
static void face_fill(int f, int idx)
{
    if (idx < 0 || idx >= news_count) return;

    const news_article_t *a = &news_articles[idx];
    const area_style_t   *s = area_style(a->area);
    lv_color_t            c = lv_color_hex(s->color);

    lv_obj_set_style_bg_color(face[f], c, LV_STATE_DEFAULT);

    lv_label_set_text(f_badge[f], s->label);
    lv_obj_set_style_text_color(f_badge[f], c, LV_STATE_DEFAULT);

    char sbuf[12];
    snprintf(sbuf, sizeof(sbuf), "%.2f", a->score);
    lv_label_set_text(f_score[f], sbuf);

    lv_label_set_text(f_title[f], a->title);
    if (a->summary[0]) {
        static char excerpt[NEWS_SUMMARY_LEN + 96];
        md_plain(a->summary, excerpt, sizeof(excerpt));
        lv_label_set_text(f_summary[f], excerpt);
    } else {
        lv_label_set_text(f_summary[f], "");
    }
    lv_label_set_text(f_source[f], a->source);

    lv_obj_set_width(f_bar[f], score_bar_w(a->score, TEXT_W));
    lv_obj_set_style_text_color(f_open[f], c, LV_STATE_DEFAULT);
}

static void peek_set_tint(int i, int idx)
{
    const area_style_t *s = area_style(
        (idx >= 0 && idx < news_count) ? news_articles[idx].area : NULL);
    lv_obj_set_style_bg_color(deck_peek[i], lv_color_hex(s->color), LV_STATE_DEFAULT);
}

/* ── Settling the deck ────────────────────────────────────────────────
 *
 * The one function that defines what "at rest" means. Every animation ends
 * here, so a flip interrupted halfway cannot leave the stack in a half-state:
 * the next step settles first, then animates from a known position. */
static void deck_settle(void)
{
    if (news_count <= 0) {
        for (int f = 0; f < 2; f++) lv_obj_add_flag(face[f], LV_OBJ_FLAG_HIDDEN);
        for (int i = 0; i < PEEK; i++) lv_obj_add_flag(deck_peek[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(lbl_deck_empty, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(lbl_counter, "—");
        return;
    }

    lv_obj_add_flag(lbl_deck_empty, LV_OBJ_FLAG_HIDDEN);

    slot_t top = slot_geom(0);
    lv_obj_clear_flag(face[face_active], LV_OBJ_FLAG_HIDDEN);
    apply_slot(face[face_active], top, top, 0);
    lv_obj_add_flag(face[1 - face_active], LV_OBJ_FLAG_HIDDEN);

    for (int i = 0; i < PEEK; i++) {
        slot_t g = slot_geom(i + 1);
        lv_obj_clear_flag(deck_peek[i], LV_OBJ_FLAG_HIDDEN);
        apply_slot(deck_peek[i], g, g, 0);
        peek_set_tint(i, deck_index(deck_pos + i + 1));
    }

    /* The top card is the only live one. Without this the ledges keep their
     * hit areas and a tap near the bottom edge opens the wrong story. */
    lv_obj_add_flag(face[face_active], LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(face[1 - face_active], LV_OBJ_FLAG_CLICKABLE);

    char buf[16];
    snprintf(buf, sizeof(buf), "%d / %d", deck_index(deck_pos) + 1, news_count);
    lv_label_set_text(lbl_counter, buf);
}

/* ── The flip ─────────────────────────────────────────────────────────
 *
 * One animation drives the whole step: a 0..256 progress that every moving
 * part reads. Animating each property of each object separately would be five
 * lv_anims that can be interrupted independently and leave the deck skewed. */
static int        flip_dir  = 1;
static int        flip_out  = 0, flip_in = 1;
static lv_color_t peek_from[PEEK], peek_to[PEEK];

static void flip_cb(void *var, int32_t t)
{
    (void)var;

    slot_t top  = slot_geom(0);
    slot_t next = slot_geom(1);
    slot_t gone = slot_geom(SLOT_GONE);

    if (flip_dir > 0) {
        /* Forward: the top card slides away and the one behind rises. */
        apply_slot(face[flip_out], top,  gone, t);
        apply_slot(face[flip_in],  next, top,  t);
    } else {
        /* Back: the exact inverse — the card returns from where it went, and
         * the one it replaced sinks into the stack. */
        apply_slot(face[flip_out], top,  next, t);
        apply_slot(face[flip_in],  gone, top,  t);
    }

    for (int i = 0; i < PEEK; i++) {
        lv_obj_set_style_bg_color(
            deck_peek[i],
            lv_color_mix(peek_to[i], peek_from[i], (lv_opa_t)(t * 255 / 256)),
            LV_STATE_DEFAULT);
    }
}

static void flip_ready_cb(lv_anim_t *a)
{
    (void)a;
    deck_settle();
}

static void step_deck(int delta)
{
    if (news_count <= 0) return;

    /* Land any flip still in flight before starting another, so a fast double
     * press steps twice rather than stacking two animations on the same
     * objects and leaving one of them stranded mid-slide. */
    if (lv_anim_del(cont_deck, flip_cb)) deck_settle();

    if (news_audio_playing) news_audio_stop();

    flip_dir = delta > 0 ? 1 : -1;
    flip_out = face_active;
    flip_in  = 1 - face_active;

    for (int i = 0; i < PEEK; i++) {
        peek_from[i] = lv_obj_get_style_bg_color(deck_peek[i], LV_PART_MAIN);
        const area_style_t *s = area_style(
            news_articles[deck_index(deck_pos + delta + i + 1)].area);
        peek_to[i] = lv_color_hex(s->color);
    }

    deck_pos += delta;
    face_fill(flip_in, deck_index(deck_pos));
    face_active = flip_in;

    lv_obj_clear_flag(face[flip_in], LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(face[flip_out], LV_OBJ_FLAG_HIDDEN);
    /* Whichever card is arriving at the front has to be drawn over the other:
     * forward the outgoing one slides off the top, back the incoming one
     * slides in over it. LVGL has no z-index, only child order. */
    lv_obj_move_foreground(face[flip_dir > 0 ? flip_out : flip_in]);

    /* Neither card takes taps mid-flip; deck_settle() gives it back. */
    lv_obj_clear_flag(face[0], LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(face[1], LV_OBJ_FLAG_CLICKABLE);

    char buf[16];
    snprintf(buf, sizeof(buf), "%d / %d", deck_index(deck_pos) + 1, news_count);
    lv_label_set_text(lbl_counter, buf);

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, cont_deck);
    lv_anim_set_exec_cb(&a, flip_cb);
    lv_anim_set_values(&a, 0, 256);
    lv_anim_set_time(&a, FLIP_MS);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_set_ready_cb(&a, flip_ready_cb);
    lv_anim_start(&a);
}

/* ── Slide transition between whole views ─────────────────────────────
 *
 * The list rides up over the deck and back down the same way, which makes the
 * swipe feel like it's moving a sheet of paper rather than cutting to a new
 * screen. The view underneath stays visible for the duration — the shells are
 * opaque, so nothing shows through.
 *
 * Opening a story does *not* use this; see the expand block below. The
 * horizontal slide survives only for paging from one story to the next. */
#define SLIDE_MS  180

static void anim_y_cb(void *obj, int32_t v)
{
    lv_obj_set_y((lv_obj_t *)obj, (lv_coord_t)v);
}

static void anim_translate_cb(void *obj, int32_t v)
{
    lv_obj_set_style_translate_x((lv_obj_t *)obj, (lv_coord_t)v, LV_STATE_DEFAULT);
}

static void anim_hide_ready_cb(lv_anim_t *a)
{
    lv_obj_add_flag((lv_obj_t *)a->var, LV_OBJ_FLAG_HIDDEN);
}

static void slide(lv_obj_t *obj, lv_anim_exec_xcb_t cb,
                  lv_coord_t from, lv_coord_t to, bool hide_after)
{
    lv_anim_del(obj, cb);      /* a fast double-swipe must not stack */

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, obj);
    lv_anim_set_exec_cb(&a, cb);
    lv_anim_set_values(&a, from, to);
    lv_anim_set_time(&a, SLIDE_MS);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    if (hide_after) lv_anim_set_ready_cb(&a, anim_hide_ready_cb);
    lv_anim_start(&a);
}

/* ── Expand: the card becomes the article ─────────────────────────────
 *
 * cont_detail is a full-screen container whose children sit at absolute
 * positions, and LVGL clips children to their parent's rectangle. So the whole
 * transition is one rectangle: start cont_detail at the tapped card's exact
 * on-screen coordinates, at the card's own corner radius and in the card's own
 * tint, and grow it to fill the panel. The story is already laid out inside;
 * what you see is the card's own top edge staying put while the article
 * unrolls out of it. The card underneath never has to be hidden, because for
 * the first frame the two are pixel-identical.
 *
 * This replaces sliding an opaque full-screen sheet in from the right, which
 * read as arriving at a different screen rather than opening the thing you
 * touched — on a panel 172 px wide, losing that thread costs more than it does
 * on a phone.
 *
 * It is affordable because it needs neither a transform nor an opacity layer:
 * position, size and radius only. LVGL 8 would render either of those to an
 * intermediate buffer first (see the note on the deck above), and full_refresh
 * means every frame is already a whole-panel blit — there is no headroom to
 * spend on a second one.
 *
 * The lateral slide survives for detail → detail, because paging to the next
 * story *is* a sideways move: there is no card on screen for it to grow from.
 *
 * ── Why this is 400 ms on an easing nobody else uses ──────────────────
 *
 * Measured on the board, not guessed at. The panel delivers an animation frame
 * every ~40 ms during the expansion (and every ~70 ms during a deck flip — the
 * flip is the *slower* of the two, because fading cards drops their opacity
 * below 255 and LVGL renders those through a layer). That is the ceiling:
 * LV_DISP_DEF_REFR_PERIOD is already 30 ms and lowering it changes nothing,
 * because the limit is render + a 220 KB full-panel blit, not the timer.
 *
 * So the only variable that matters is **how far the rectangle moves per
 * frame**, which is duration and easing, not cost. At 240 ms this got 6 frames,
 * and lv_anim_path_ease_out puts 38% of the whole distance into the first one
 * — a 364 px growth arriving as a 138 px jump, then a crawl. That is what
 * "glitchy" was. The same easing is fine on the deck flip only because the
 * card moves 74 px in total, so 38% of it is nothing.
 *
 * 400 ms buys ~10 frames, and path_expand() spreads them almost evenly with a
 * soft landing: worst frame 11% of the distance instead of 38%. */
#define EXPAND_MS    400
/* Closing is deliberately faster than opening. Opening is the moment worth
 * dwelling on; leaving is not, and cont_detail keeps its clickable children
 * (detail_body has to stay hittable or the article stops scrolling) for the
 * whole collapse, so every millisecond of it is a millisecond in which a tap
 * on the deck behind lands on a view that is on its way out. */
#define COLLAPSE_MS  260

/* Nearly linear, decelerating gently into place. LVGL's stock paths are built
 * for 60 fps, where a front-loaded curve reads as responsiveness; at 25 fps it
 * reads as a dropped frame. Control values are the same 0..1024 scale
 * lv_anim_path_ease_out uses. */
static int32_t path_expand(const lv_anim_t *a)
{
    uint32_t t    = lv_map(a->act_time, 0, a->time, 0, LV_BEZIER_VAL_MAX);
    int32_t  step = lv_bezier3(t, 0, 300, 800, LV_BEZIER_VAL_MAX);
    return a->start_value +
           (((int32_t)step * (a->end_value - a->start_value)) >> LV_BEZIER_VAL_SHIFT);
}

/* The card this story grew out of. Its coordinates are read fresh at collapse
 * time rather than stored, so a list that has been scrolled since still
 * shrinks back to the right place. */
static lv_obj_t *detail_origin = NULL;

/* ── One rule: the detail view never teleports ────────────────────────
 *
 * det_cur is where cont_detail actually is, maintained here rather than read
 * back with lv_obj_get_coords(). Two reasons, and the second one is the bug
 * this whole section exists to kill.
 *
 * The mundane reason: lv_obj_set_pos() does not update an object's coordinates
 * immediately, it marks the layout dirty and lets the next refresh resolve it.
 * Setting a rect and reading it back in the same call gives the *old* one.
 *
 * The real reason: every animation must start from wherever the view currently
 * is, never from a fixed start value. lv_anim_start() applies start_value
 * immediately (early_apply is on by default in lv_anim_init), so an animation
 * expressed as "0 -> 256 of the journey from the card to full screen" snaps the
 * view to the card the instant it starts — and one expressed as "256 -> 0"
 * snaps it to *full screen*. Restart either one while the other is in flight
 * and you get a single frame of the destination before the animation plays,
 * which is exactly the "it jumps to the article, then reverts to do the
 * animation" flicker. On the light default theme (LV_THEME_DEFAULT_DARK 0) a
 * pale story tint flashing full-screen reads as white.
 *
 * So: interpolate between two *areas*, capture `from` at the moment of
 * starting, and there is no start value left to teleport to. */
static lv_area_t  det_cur;
static lv_coord_t det_rad_cur;
static lv_area_t  det_from, det_to;
static lv_coord_t det_rad_from, det_rad_to;

static void area_set(lv_area_t *a, lv_coord_t x, lv_coord_t y,
                     lv_coord_t w, lv_coord_t h)
{
    a->x1 = x; a->y1 = y; a->x2 = x + w - 1; a->y2 = y + h - 1;
}

static void detail_full_area(lv_area_t *a)
{
    area_set(a, 0, 0, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES);
}

/* The single place cont_detail's geometry changes. cont_detail's parent is the
 * screen at (0,0), so absolute and parent-relative coordinates are the same
 * here; that is what lets an origin captured with lv_obj_get_coords() be
 * handed straight to lv_obj_set_pos(). */
static void detail_set_rect(const lv_area_t *a, lv_coord_t radius)
{
    det_cur     = *a;
    det_rad_cur = radius;
    lv_obj_set_pos(cont_detail, a->x1, a->y1);
    lv_obj_set_size(cont_detail, lv_area_get_width(a), lv_area_get_height(a));
    lv_obj_set_style_radius(cont_detail, radius, LV_STATE_DEFAULT);
}

static bool detail_is_full(void)
{
    return det_cur.x1 == 0 && det_cur.y1 == 0 &&
           lv_area_get_width(&det_cur)  == EXAMPLE_LCD_H_RES &&
           lv_area_get_height(&det_cur) == EXAMPLE_LCD_V_RES;
}

static void rect_cb(void *obj, int32_t t)
{
    (void)obj;
    lv_area_t a;
    area_set(&a,
             lerp(det_from.x1, det_to.x1, t),
             lerp(det_from.y1, det_to.y1, t),
             lerp(lv_area_get_width(&det_from),  lv_area_get_width(&det_to),  t),
             lerp(lv_area_get_height(&det_from), lv_area_get_height(&det_to), t));
    detail_set_rect(&a, lerp(det_rad_from, det_rad_to, t));
}

/* Where a story should grow from, or shrink back into. False means there is no
 * sensible rect — the origin card was hidden by a shorter refresh, or the
 * story was reached by paging rather than by a tap. */
static bool origin_rect(lv_obj_t *origin, lv_area_t *out)
{
    if (!origin) return false;
    if (lv_obj_has_flag(origin, LV_OBJ_FLAG_HIDDEN)) return false;
    lv_obj_get_coords(origin, out);
    return lv_area_get_width(out) > 0 && lv_area_get_height(out) > 0;
}

/* Put the detail view away *now* — no animation, no callback.
 *
 * Every path that leaves a story ends here, including the animated one. The
 * hide must never be reachable only from an lv_anim ready callback: a deleted
 * or interrupted animation does not fire one, and cont_detail is a
 * full-screen clickable object sitting on top of everything else. A hide that
 * gets skipped therefore does not look like a missed animation, it looks like
 * the entire UI has stopped responding to taps. */
static void detail_park(void)
{
    lv_area_t full;
    lv_anim_del(cont_detail, rect_cb);
    lv_obj_add_flag(cont_detail, LV_OBJ_FLAG_HIDDEN);
    detail_full_area(&full);
    detail_set_rect(&full, 0);
    /* A paging slide cut short by a close would otherwise leave the prose
     * offset, and the next story would open already shoved sideways. */
    lv_anim_del(detail_body, anim_translate_cb);
    lv_obj_set_style_translate_x(detail_body, 0, LV_STATE_DEFAULT);
    detail_origin = NULL;
}

static void collapse_ready_cb(lv_anim_t *a)
{
    (void)a;
    detail_park();
}

static void expand_ready_cb(lv_anim_t *a)
{
    lv_area_t full;
    (void)a;
    detail_full_area(&full);
    detail_set_rect(&full, 0);   /* land exactly, whatever the lerp rounded to */
}

/* Animate from wherever the view is now to `to`. */
static void detail_move_to(const lv_area_t *to, lv_coord_t radius,
                           uint32_t base_ms, lv_anim_ready_cb_t ready)
{
    lv_anim_del(cont_detail, rect_cb);

    det_from     = det_cur;
    det_rad_from = det_rad_cur;
    det_to       = *to;
    det_rad_to   = radius;

    /* Scale the time to the distance actually left to travel. A transition
     * interrupted near the end has 30 px to go and must not spend the full
     * EXPAND_MS crawling through it. */
    lv_coord_t span = lv_area_get_height(&det_to) - lv_area_get_height(&det_from);
    if (span < 0) span = -span;
    uint32_t ms = base_ms * span / (EXAMPLE_LCD_V_RES - CARD_H);
    if (ms < 90)      ms = 90;
    if (ms > base_ms) ms = base_ms;

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, cont_detail);
    lv_anim_set_exec_cb(&a, rect_cb);
    lv_anim_set_values(&a, 0, 256);      /* 0 == det_from == where it is now */
    lv_anim_set_time(&a, ms);
    lv_anim_set_path_cb(&a, path_expand);
    lv_anim_set_ready_cb(&a, ready);
    lv_anim_start(&a);
}

/* Shrink the story back into the card it came from, if that card is still
 * there to shrink into. */
static void detail_dismiss(void)
{
    lv_area_t back;

    if (lv_obj_has_flag(cont_detail, LV_OBJ_FLAG_HIDDEN)) return;

    /* Stop taking touches the moment it starts leaving. It is still a
     * full-screen object for the length of the collapse, and leaving it
     * clickable means every tap during that time lands on a view that is on
     * its way out instead of on the deck appearing behind it. */
    lv_obj_clear_flag(cont_detail, LV_OBJ_FLAG_CLICKABLE);

    if (!origin_rect(detail_origin, &back)) { detail_park(); return; }

    detail_move_to(&back, CARD_RADIUS, COLLAPSE_MS, collapse_ready_cb);
}

/* ── Paging inside an open story ──────────────────────────────────────
 *
 * Swiping to the next story used to slide cont_detail itself: park it off the
 * right edge, then bring it back. That exposes whatever is behind it for the
 * frame before it returns — the deck flashing through a story that has not
 * gone anywhere. Translating the prose instead keeps the full-screen tint
 * stationary and opaque, so the new text arrives over the story's own colour
 * and nothing behind is ever uncovered.
 *
 * translate_x is a draw-time offset, not a transform, so unlike transform_zoom
 * it needs no intermediate layer — which is the only reason it is affordable
 * here (see the note on the deck). */
#define PAGE_SHIFT  44
#define PAGE_MS    200

static void page_body_in(void)
{
    lv_anim_del(detail_body, anim_translate_cb);

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, detail_body);
    lv_anim_set_exec_cb(&a, anim_translate_cb);
    lv_anim_set_values(&a, PAGE_SHIFT, 0);
    lv_anim_set_time(&a, PAGE_MS);
    lv_anim_set_path_cb(&a, path_expand);
    lv_anim_start(&a);
}

/* ── View switching ───────────────────────────────────────────────── */
static void show_deck(void)
{
    view = VIEW_DECK;
    lv_obj_clear_flag(cont_deck, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_pos(cont_deck, 0, 0);

    /* Ride the list back down only if it is actually up there. Returning from
     * a story that was opened on the deck it is already parked, and animating
     * it anyway would drag a full-screen opaque panel down across the very
     * deck the story is shrinking into. */
    if (!lv_obj_has_flag(cont_list, LV_OBJ_FLAG_HIDDEN)) {
        lv_obj_set_x(cont_list, 0);
        slide(cont_list, anim_y_cb, lv_obj_get_y(cont_list),
              EXAMPLE_LCD_V_RES, true);
    }
    detail_dismiss();
}

static void show_list(void)
{
    view = VIEW_LIST;

    /* Same reasoning in reverse: coming back from a story that was opened *in*
     * the list must not replay the list's own entrance underneath it. Animate
     * from wherever the list currently is, and skip it entirely when it is
     * already at rest in place. */
    bool in_place = !lv_obj_has_flag(cont_list, LV_OBJ_FLAG_HIDDEN) &&
                    lv_obj_get_y(cont_list) == 0 &&
                    lv_anim_get(cont_list, anim_y_cb) == NULL;

    lv_obj_clear_flag(cont_list, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_x(cont_list, 0);
    if (!in_place) slide(cont_list, anim_y_cb, lv_obj_get_y(cont_list), 0, false);

    detail_dismiss();
}

/* origin is the card that was tapped, and NULL when there wasn't one. */
static void show_detail(int idx, lv_obj_t *origin)
{
    if (idx < 0 || idx >= news_count) return;
    selected    = idx;
    detail_from = (view == VIEW_DETAIL) ? detail_from : view;
    view        = VIEW_DETAIL;

    const news_article_t *a = &news_articles[idx];
    const area_style_t   *s = area_style(a->area);
    lv_color_t            c = lv_color_hex(s->color);

    /* Same colour as the card it came from, edge to edge — the first frame of
     * the expansion has to be indistinguishable from the card itself. */
    lv_obj_set_style_bg_color(cont_detail, c, LV_STATE_DEFAULT);

    lv_label_set_text(lbl_d_badge, s->label);
    lv_obj_set_style_text_color(lbl_d_badge, c, LV_STATE_DEFAULT);
    lv_obj_set_style_text_color(lbl_back, c, LV_STATE_DEFAULT);
    lv_obj_set_style_text_color(lbl_listen, c, LV_STATE_DEFAULT);

    char sbuf[12];
    snprintf(sbuf, sizeof(sbuf), "%.2f", a->score);
    lv_label_set_text(lbl_d_score, sbuf);

    lv_label_set_text(lbl_d_title, a->title);
    lv_label_set_text(lbl_d_source, a->source);

    /* The empty case is real: a fetch failure leaves the RSS summary, and some
     * feeds ship nothing worth showing. */
    if (a->summary[0]) {
        static char clean[NEWS_SUMMARY_LEN + 96];
        static char body[NEWS_SUMMARY_LEN + 160];

        md_strip(a->summary, clean, sizeof(clean), true);

        /* Only pace it ourselves when the text arrived as one block. Summaries
         * cached under PROMPT_VERSION 1 have no breaks at all and still need
         * them; anything written since brings its own, and inserting more on
         * top would cut the model's own paragraphs in half. */
        if (strchr(clean, '\n')) {
            lv_label_set_text(lbl_d_summary, clean);
        } else {
            paragraphize(clean, body, sizeof(body));
            lv_label_set_text(lbl_d_summary, body);
        }
    } else {
        lv_label_set_text(lbl_d_summary, "(no summary in the feed)");
    }

    /* Always open a story at the top, regardless of where the last one was
     * left. */
    lv_obj_scroll_to_y(detail_body, 0, LV_ANIM_OFF);

    bool      was_open = !lv_obj_has_flag(cont_detail, LV_OBJ_FLAG_HIDDEN);
    lv_area_t start, full;

    detail_full_area(&full);
    lv_obj_clear_flag(cont_detail, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(cont_detail, LV_OBJ_FLAG_CLICKABLE);

    if (was_open) {
        /* Already on screen. Two cases, and neither may reset the geometry:
         * settled at full screen this is paging, which slides the prose only
         * (see page_body_in) so nothing ever uncovers the view behind; caught
         * mid-collapse, the right answer is to finish opening from wherever
         * the shrink got to. detail_origin is deliberately left alone — swiping
         * back out should land on the card the reader actually came from, not
         * on whichever story they paged to. */
        if (detail_is_full()) page_body_in();
        else                  detail_move_to(&full, 0, EXPAND_MS, expand_ready_cb);
        return;
    }

    detail_origin = origin;

    if (!origin_rect(origin, &start)) {
        /* No card to grow out of. Grow from a card-sized rect in the middle
         * rather than snapping to full screen — this path is rare, but a snap
         * here is the same one-frame flash the whole design avoids. */
        area_set(&start, PAD, (EXAMPLE_LCD_V_RES - CARD_H) / 2, BODY_W, CARD_H);
        detail_origin = NULL;
    }

    detail_set_rect(&start, CARD_RADIUS);
    detail_move_to(&full, 0, EXPAND_MS, expand_ready_cb);
}

static void close_detail(void)
{
    /* Leaving a story stops its narration — otherwise audio keeps playing over
     * a deck you're already flipping through. */
    if (news_audio_playing) news_audio_stop();
    if (detail_from == VIEW_LIST) show_list();
    else                         show_deck();
}

/* ── Event callbacks ──────────────────────────────────────────────── */
static int card_index_of(lv_obj_t *obj)
{
    for (int i = 0; i < NEWS_MAX_ARTICLES; i++) {
        if (card[i] == obj) return i;
    }
    return -1;
}

static void cb_card(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    /* LVGL suppresses CLICKED when the press turned into a scroll, so tapping
     * and dragging on the same card do the right thing without extra work. */
    lv_obj_t *obj = lv_event_get_target(e);
    int       idx = card_index_of(obj);
    if (idx >= 0) show_detail(idx, obj);   /* the story grows out of this card */
}

static void cb_face(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    show_detail(deck_index(deck_pos), lv_event_get_target(e));
}

static void cb_prev(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    step_deck(-1);
}

static void cb_next(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    step_deck(1);
}

static void cb_back(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    close_detail();
}

static void cb_listen(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    /* Both branches only set a flag; audio_task does the work. */
    if (news_audio_playing) news_audio_stop();
    else if (selected >= 0)  news_audio_play(selected);
}

/* Gestures. The deck has nothing scrollable in it, so all four directions are
 * free there. The list and the detail body both scroll vertically, so on those
 * two only the horizontal ones are reliable — LVGL suppresses a gesture when
 * the press became a scroll, and near the top of a list a downward drag is
 * usually classified as an elastic scroll rather than a gesture. That is why
 * swipe *right* is the way back out of the list, matching the detail view,
 * with down accepted as well for when it does come through. */
static void cb_gesture(lv_event_t *e)
{
    (void)e;
    lv_indev_t *indev = lv_indev_get_act();
    lv_dir_t    dir   = lv_indev_get_gesture_dir(indev);

    /* A gesture and a click are not alternatives — LVGL sends the gesture
     * while the finger is moving and still sends CLICKED when it lifts, so
     * swiping up on a card ran show_list() and then immediately had the
     * card's own handler open the story on top of it. Telling the indev to
     * wait for release ends the press here, and no click follows. */
    if (dir != LV_DIR_NONE) lv_indev_wait_release(indev);

    switch (view) {
        case VIEW_DECK:
            if      (dir == LV_DIR_LEFT)  step_deck(1);
            else if (dir == LV_DIR_RIGHT) step_deck(-1);
            else if (dir == LV_DIR_TOP)   show_list();
            break;

        case VIEW_LIST:
            if (dir == LV_DIR_RIGHT || dir == LV_DIR_BOTTOM) show_deck();
            break;

        case VIEW_DETAIL:
            if (dir == LV_DIR_RIGHT) {                   /* back, iOS-style */
                close_detail();
            } else if (dir == LV_DIR_LEFT) {             /* next story */
                if (selected + 1 < news_count) {
                    if (news_audio_playing) news_audio_stop();
                    show_detail(selected + 1, NULL);
                }
            }
            break;
    }
}

/* ── Rendering ────────────────────────────────────────────────────── */
static void render_list(void)
{
    for (int i = 0; i < NEWS_MAX_ARTICLES; i++) {
        if (i >= news_count) {
            lv_obj_add_flag(card[i], LV_OBJ_FLAG_HIDDEN);   /* skipped by flex */
            continue;
        }

        const news_article_t *a = &news_articles[i];
        const area_style_t   *s = area_style(a->area);
        lv_color_t            c = lv_color_hex(s->color);

        lv_obj_set_style_bg_color(card[i], c, LV_STATE_DEFAULT);

        lv_label_set_text(card_badge[i], s->label);
        lv_obj_set_style_text_color(card_badge[i], c, LV_STATE_DEFAULT);

        char sbuf[12];
        snprintf(sbuf, sizeof(sbuf), "%.2f", a->score);
        lv_label_set_text(card_score[i], sbuf);

        lv_label_set_text(card_title[i], a->title);
        lv_label_set_text(card_source[i], a->source);

        lv_obj_set_width(card_bar[i], score_bar_w(a->score, TEXT_W));
        lv_obj_clear_flag(card[i], LV_OBJ_FLAG_HIDDEN);
    }

    lv_obj_scroll_to_y(list_body, 0, LV_ANIM_OFF);
}

static void render_status(void)
{
    char buf[32];

    /* Checked before news_count: a rebuild runs with a full deck on screen, and
     * "10 stories" for 30 s would read as a dead button. */
    if (news_rebuilding) {
        snprintf(buf, sizeof(buf), "refreshing...");
    } else if (news_count > 0) {
        snprintf(buf, sizeof(buf), "%d stories", news_count);
    } else if (news_fetch_failed) {
        snprintf(buf, sizeof(buf), "fetch failed");
    } else if (wifi_conn_status != WIFI_CONNECTED) {
        snprintf(buf, sizeof(buf), "no wi-fi");
    } else {
        snprintf(buf, sizeof(buf), "loading...");
    }
    lv_label_set_text(lbl_status, buf);
}

/* ── BOOT button (GPIO 0): back out of a story, or rebuild the digest ──
 *
 * On the deck and the list this asks the backend to re-run the whole pipeline,
 * not just to re-GET digest.json — the RSS side only updates when the pipeline
 * runs (the launchd job at 08:00, or this button), so a plain re-fetch would
 * redraw the same stories and look like the button did nothing. Takes 20-30 s;
 * the status line says "refreshing" for the duration. This is the widget's ↻,
 * and it means the same thing there. */
static void poll_boot_button(void)
{
    static bool was_down = false;
    bool down = (digitalRead(BOOT_BUTTON_GPIO) == LOW);

    if (down && !was_down) {           /* 250 ms polling is its own debounce */
        if      (view == VIEW_DETAIL) close_detail();
        else if (!news_rebuilding)    news_client_request_rebuild();
    }
    was_down = down;
}

static void ui_timer_cb(lv_timer_t *t)
{
    (void)t;

    if (news_data_version != last_version) {
        last_version = news_data_version;
        render_list();

        /* A refresh can land mid-flip and mid-story. Put the deck back on a
         * real article and settle it rather than leaving it pointing into an
         * array that just changed length. */
        lv_anim_del(cont_deck, flip_cb);
        if (news_count > 0) face_fill(face_active, deck_index(deck_pos));
        deck_settle();

        /* If an open story vanished from a smaller refresh, fall back. */
        if (view == VIEW_DETAIL && selected >= news_count) close_detail();
    }

    render_status();
    poll_boot_button();

    /* Track playback state so the button says what it will do next. */
    if (view == VIEW_DETAIL) {
        const char *txt = news_audio_playing ? LV_SYMBOL_STOP "  STOP"
                        : news_audio_failed  ? LV_SYMBOL_WARNING "  AUDIO FAILED"
                                             : LV_SYMBOL_PLAY "  LISTEN";
        lv_label_set_text(lbl_listen, txt);
        lv_obj_align(lbl_listen, LV_ALIGN_CENTER, 0, 0);
    }
}

/* ── Shared style helpers ─────────────────────────────────────────── */
static void strip_chrome(lv_obj_t *o)
{
    lv_obj_set_style_border_width(o, 0, LV_STATE_DEFAULT);
    lv_obj_set_style_shadow_width(o, 0, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_opa(o, LV_OPA_TRANSP, LV_STATE_DEFAULT);
    lv_obj_set_style_pad_all(o, 0, LV_STATE_DEFAULT);
    lv_obj_set_style_radius(o, 0, LV_STATE_DEFAULT);
}

/* ── No hover on a touchscreen ────────────────────────────────────────
 *
 * LVGL's default theme gives every button a pressed style (a darkening colour
 * filter) plus two style *transitions*: transition_delayed on the way in and
 * transition_normal on the way out. That pair is built for a mouse, where a
 * control lights up as the cursor arrives and fades back as it leaves. A
 * finger has no hover — it is down or it is not — so the delay and the fade
 * only leave a highlight sitting on a control after the touch that caused it
 * is over, which reads as a state the UI got stuck in.
 *
 * Two ways of switching that off do not work, both found the hard way:
 *
 *   lv_obj_set_style_transition(o, NULL, sel) looks like the API for "no
 *   transition" and is a null dereference. lv_obj_set_state() reads the
 *   property and walks tr->props with no NULL check, so the object crashes on
 *   its first state change — i.e. the first time the button is pressed.
 *
 *   Overriding it with an empty descriptor does not help either: the same
 *   function collects transitions from *every* style on the object and keeps
 *   the one whose selector has the highest state, so the theme's PRESSED
 *   entry outranks anything set at the default state.
 *
 * Taking the theme's styles off the object is what actually works. Feedback
 * itself is still worth keeping, and more here than on a fast display — at
 * ~37 ms a frame, a tap with no acknowledgement feels ignored — so the pressed
 * *colour* stays and only the filter and its timing go, which makes the
 * response instant rather than animated. Tiles get nothing at all: tapping one
 * begins the expansion, and that is the acknowledgement. */
static lv_obj_t *make_button(lv_obj_t *parent, lv_coord_t w, lv_coord_t h,
                             lv_color_t bg, lv_color_t bg_pressed,
                             lv_event_cb_t cb)
{
    lv_obj_t *b = lv_btn_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_size(b, w, h);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(b, bg, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(b, bg_pressed, LV_STATE_PRESSED);
    lv_obj_clear_flag(b, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, NULL);
    return b;
}

static lv_obj_t *make_label(lv_obj_t *parent, const lv_font_t *font,
                            lv_color_t color, lv_coord_t width)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_label_set_text(l, "");
    lv_obj_set_style_text_font(l, font, LV_STATE_DEFAULT);
    lv_obj_set_style_text_color(l, color, LV_STATE_DEFAULT);
    if (width > 0) lv_obj_set_width(l, width);
    return l;
}

/* The badge. Small uppercase in the card's own tint, knocked out of an ink
 * capsule — the one shape that stays legible on all nine tints. */
static lv_obj_t *make_badge(lv_obj_t *parent)
{
    lv_obj_t *l = make_label(parent, FONT_META, CLR_INK, 0);
    lv_obj_set_style_text_letter_space(l, META_TRACKING, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_opa(l, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(l, CLR_INK, LV_STATE_DEFAULT);
    lv_obj_set_style_radius(l, LV_RADIUS_CIRCLE, LV_STATE_DEFAULT);
    lv_obj_set_style_pad_hor(l, 7, LV_STATE_DEFAULT);
    lv_obj_set_style_pad_ver(l, 3, LV_STATE_DEFAULT);
    return l;
}

/* A round ink chip with a symbol in it — the open affordance on a card, and
 * the back button in the detail view. */
static lv_obj_t *make_chip(lv_obj_t *parent, lv_coord_t d, const char *sym,
                           lv_obj_t **out_label)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_set_size(o, d, d);
    strip_chrome(o);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(o, CLR_INK, LV_STATE_DEFAULT);
    lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, LV_STATE_DEFAULT);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *l = make_label(o, FONT_META, CLR_ACCENT, 0);
    lv_label_set_text(l, sym);
    lv_obj_align(l, LV_ALIGN_CENTER, 0, 0);
    if (out_label) *out_label = l;
    return o;
}

/* The score bar's groove and fill, on a card. */
static lv_obj_t *make_bar(lv_obj_t *parent, lv_coord_t x, lv_coord_t y,
                          lv_coord_t track_w)
{
    lv_obj_t *track = lv_obj_create(parent);
    lv_obj_set_size(track, track_w, BAR_H);
    lv_obj_set_pos(track, x, y);
    strip_chrome(track);
    lv_obj_set_style_bg_opa(track, INK_BAR_TRACK, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(track, CLR_INK, LV_STATE_DEFAULT);
    lv_obj_set_style_radius(track, BAR_H / 2, LV_STATE_DEFAULT);
    lv_obj_clear_flag(track, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *fill = lv_obj_create(track);
    lv_obj_set_size(fill, 3, BAR_H);
    lv_obj_set_pos(fill, 0, 0);
    strip_chrome(fill);
    lv_obj_set_style_bg_opa(fill, INK_BAR_FILL, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(fill, CLR_INK, LV_STATE_DEFAULT);
    lv_obj_set_style_radius(fill, BAR_H / 2, LV_STATE_DEFAULT);
    lv_obj_clear_flag(fill, LV_OBJ_FLAG_SCROLLABLE);
    return fill;
}

/* ── Header, shared by the deck and the list ──────────────────────── */
static lv_obj_t *build_header(lv_obj_t *parent, bool with_status)
{
    lv_obj_t *hdr = lv_obj_create(parent);
    lv_obj_set_size(hdr, EXAMPLE_LCD_H_RES, HEADER_H);
    lv_obj_set_pos(hdr, 0, 0);
    strip_chrome(hdr);
    lv_obj_clear_flag(hdr, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *ttl = make_label(hdr, FONT_DISPLAY, CLR_ACCENT, 0);
    lv_obj_set_style_text_letter_space(ttl, 2, LV_STATE_DEFAULT);
    lv_label_set_text(ttl, "NEWS");
    lv_obj_align(ttl, LV_ALIGN_LEFT_MID, PAD, 0);

    if (with_status) {
        lbl_status = make_label(hdr, FONT_META, CLR_DIM, 0);
        lv_label_set_text(lbl_status, "loading...");
        lv_obj_align(lbl_status, LV_ALIGN_RIGHT_MID, -PAD, 0);
    }
    return hdr;
}

/* ── DECK view ────────────────────────────────────────────────────── */
static void build_card_face(int f)
{
    face[f] = lv_obj_create(cont_deck);
    lv_obj_set_size(face[f], BODY_W, CARD_H);
    strip_chrome(face[f]);
    lv_obj_set_style_bg_opa(face[f], LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_radius(face[f], CARD_RADIUS, LV_STATE_DEFAULT);
    /* Two adjacent cards in neighbouring tints — amber behind amber —
     * otherwise merge into one shape at the ledge where they overlap. A dark
     * hairline is enough to keep them countable without reading as a border. */
    lv_obj_set_style_border_width(face[f], 1, LV_STATE_DEFAULT);
    lv_obj_set_style_border_color(face[f], CLR_BG, LV_STATE_DEFAULT);
    lv_obj_set_style_border_opa(face[f], 140, LV_STATE_DEFAULT);
    lv_obj_clear_flag(face[f], LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(face[f], LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(face[f], cb_face, LV_EVENT_CLICKED, NULL);

    /* Ordered the way the reference layouts order it: what this is and how
     * strongly, then the headline doing all the work, then the provenance.
     * Positions are absolute rather than flex — the face is a fixed size and
     * every ledge in the stack has to line up with it exactly. */
    f_badge[f] = make_badge(face[f]);
    lv_obj_set_pos(f_badge[f], CARD_PAD, CARD_PAD);

    /* The score is the reason this story is on top of the deck, so it gets the
     * weight of a headline number rather than being tucked away as metadata. */
    f_score[f] = make_label(face[f], FONT_TITLE, CLR_INK, 0);
    lv_obj_align(f_score[f], LV_ALIGN_TOP_RIGHT, -CARD_PAD, CARD_PAD - 1);

    f_title[f] = make_label(face[f], FONT_TITLE, CLR_INK, TEXT_W);
    lv_obj_set_pos(f_title[f], CARD_PAD, CARD_PAD + 30);
    lv_obj_set_height(f_title[f], 105);
    lv_label_set_long_mode(f_title[f], LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_line_space(f_title[f], TITLE_LEADING, LV_STATE_DEFAULT);

    f_summary[f] = make_label(face[f], FONT_BODY, CLR_INK, TEXT_W);
    lv_obj_set_pos(f_summary[f], CARD_PAD, CARD_PAD + 141);
    lv_obj_set_height(f_summary[f], 62);
    lv_label_set_long_mode(f_summary[f], LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_opa(f_summary[f], INK_SOFT, LV_STATE_DEFAULT);
    lv_obj_set_style_text_line_space(f_summary[f], CARD_BODY_LEADING, LV_STATE_DEFAULT);

    f_bar[f] = make_bar(face[f], CARD_PAD, CARD_H - CARD_PAD - 22 - 9 - BAR_H,
                        TEXT_W);

    f_source[f] = make_label(face[f], FONT_META, CLR_INK, TEXT_W - 28);
    lv_obj_set_pos(f_source[f], CARD_PAD, CARD_H - CARD_PAD - 18);
    lv_label_set_long_mode(f_source[f], LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_opa(f_source[f], INK_SOURCE, LV_STATE_DEFAULT);
    lv_obj_set_style_text_letter_space(f_source[f], META_TRACKING, LV_STATE_DEFAULT);

    /* Affordance only — the whole card is the hit target. It is here because a
     * flat coloured rectangle gives no sign there is anything underneath it. */
    lv_obj_t *chip = make_chip(face[f], 22, LV_SYMBOL_RIGHT, &f_open[f]);
    lv_obj_align(chip, LV_ALIGN_BOTTOM_RIGHT, -CARD_PAD, -CARD_PAD);
}

static void build_deck_view(lv_obj_t *scr)
{
    cont_deck = lv_obj_create(scr);
    lv_obj_set_size(cont_deck, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES);
    lv_obj_set_pos(cont_deck, 0, 0);
    strip_chrome(cont_deck);
    lv_obj_set_style_bg_opa(cont_deck, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(cont_deck, CLR_BG, LV_STATE_DEFAULT);
    lv_obj_clear_flag(cont_deck, LV_OBJ_FLAG_SCROLLABLE);

    build_header(cont_deck, true);

    /* The deck body starts below the header. Cards are positioned in screen
     * coordinates inside cont_deck, so slot_geom() can stay arithmetic. */
    lv_obj_t *rule = lv_obj_create(cont_deck);
    lv_obj_set_size(rule, EXAMPLE_LCD_H_RES, 1);
    lv_obj_set_pos(rule, 0, HEADER_H);
    strip_chrome(rule);
    lv_obj_set_style_bg_opa(rule, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(rule, CLR_RULE, LV_STATE_DEFAULT);

    /* Ledges first: LVGL has no z-index, only child order, so everything
     * created after this draws on top of it. Deepest ledge first. */
    for (int i = PEEK - 1; i >= 0; i--) {
        deck_peek[i] = lv_obj_create(cont_deck);
        lv_obj_set_size(deck_peek[i], BODY_W - (i + 1) * SHRINK_STEP, CARD_H);
        strip_chrome(deck_peek[i]);
        lv_obj_set_style_bg_opa(deck_peek[i], LV_OPA_COVER, LV_STATE_DEFAULT);
        lv_obj_set_style_radius(deck_peek[i], CARD_RADIUS, LV_STATE_DEFAULT);
        lv_obj_set_style_border_width(deck_peek[i], 1, LV_STATE_DEFAULT);
        lv_obj_set_style_border_color(deck_peek[i], CLR_BG, LV_STATE_DEFAULT);
        lv_obj_set_style_border_opa(deck_peek[i], 140, LV_STATE_DEFAULT);
        lv_obj_clear_flag(deck_peek[i], LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_clear_flag(deck_peek[i], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_flag(deck_peek[i], LV_OBJ_FLAG_HIDDEN);
    }

    build_card_face(0);
    build_card_face(1);

    /* Empty state. The cause is almost always one of two things: the backend
     * is not running, or it is on a different port than this expects. */
    lbl_deck_empty = make_label(cont_deck, FONT_BODY, CLR_DIM, BODY_W);
    lv_label_set_long_mode(lbl_deck_empty, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(lbl_deck_empty, LV_TEXT_ALIGN_CENTER, LV_STATE_DEFAULT);
    lv_label_set_text(lbl_deck_empty, "No stories yet.\n\nStart the backend with\nesp-serve --port 8010");
    lv_obj_set_pos(lbl_deck_empty, PAD, DECK_TOP + 60);

    /* Nav bar — fixed, so the controls never move. An earlier sketch on the
     * widget put next on the card itself, which meant the button flew away and
     * came back every time you pressed it. */
    lv_obj_t *nav = lv_obj_create(cont_deck);
    lv_obj_set_size(nav, EXAMPLE_LCD_H_RES, NAV_H);
    lv_obj_set_pos(nav, 0, EXAMPLE_LCD_V_RES - NAV_H);
    strip_chrome(nav);
    lv_obj_clear_flag(nav, LV_OBJ_FLAG_SCROLLABLE);

    /* 36 px, not the widget's 24: this one is hit with a finger. */
    btn_prev = make_button(nav, 36, 36, CLR_SURFACE, CLR_RULE, cb_prev);
    lv_obj_set_style_radius(btn_prev, LV_RADIUS_CIRCLE, LV_STATE_DEFAULT);
    lv_obj_align(btn_prev, LV_ALIGN_LEFT_MID, PAD, 0);
    lv_obj_t *l_prev = make_label(btn_prev, FONT_META, CLR_DIM, 0);
    lv_label_set_text(l_prev, LV_SYMBOL_LEFT);
    lv_obj_align(l_prev, LV_ALIGN_CENTER, 0, 0);

    lbl_counter = make_label(nav, FONT_META, CLR_DIM, 0);
    lv_label_set_text(lbl_counter, "—");
    lv_obj_align(lbl_counter, LV_ALIGN_CENTER, 0, 0);

    btn_next = make_button(nav, 36, 36, CLR_ACCENT, CLR_DIM, cb_next);
    lv_obj_set_style_radius(btn_next, LV_RADIUS_CIRCLE, LV_STATE_DEFAULT);
    lv_obj_align(btn_next, LV_ALIGN_RIGHT_MID, -PAD, 0);
    lv_obj_t *l_next = make_label(btn_next, FONT_META, CLR_BG, 0);
    lv_label_set_text(l_next, LV_SYMBOL_RIGHT);
    lv_obj_align(l_next, LV_ALIGN_CENTER, 0, 0);
}

/* ── LIST view ────────────────────────────────────────────────────── */
static void build_list_view(lv_obj_t *scr)
{
    cont_list = lv_obj_create(scr);
    lv_obj_set_size(cont_list, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES);
    lv_obj_set_pos(cont_list, 0, EXAMPLE_LCD_V_RES);   /* parked below */
    strip_chrome(cont_list);
    lv_obj_set_style_bg_opa(cont_list, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(cont_list, CLR_BG, LV_STATE_DEFAULT);
    lv_obj_clear_flag(cont_list, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(cont_list, LV_OBJ_FLAG_HIDDEN);

    build_header(cont_list, false);

    lv_obj_t *rule = lv_obj_create(cont_list);
    lv_obj_set_size(rule, EXAMPLE_LCD_H_RES, 1);
    lv_obj_set_pos(rule, 0, HEADER_H);
    strip_chrome(rule);
    lv_obj_set_style_bg_opa(rule, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(rule, CLR_RULE, LV_STATE_DEFAULT);

    /* Scrollable card column. sim_main.c reaches this as child 2 of child 1 of
     * the screen — keep cont_list second on the screen and this third here. */
    list_body = lv_obj_create(cont_list);
    lv_obj_set_size(list_body, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES - HEADER_H - 1);
    lv_obj_set_pos(list_body, 0, HEADER_H + 1);
    strip_chrome(list_body);
    lv_obj_set_style_pad_all(list_body, PAD, LV_STATE_DEFAULT);
    lv_obj_set_style_pad_row(list_body, PAD, LV_STATE_DEFAULT);
    lv_obj_set_flex_flow(list_body, LV_FLEX_FLOW_COLUMN);
    lv_obj_add_flag(list_body, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(list_body, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(list_body, LV_SCROLLBAR_MODE_AUTO);

    for (int i = 0; i < NEWS_MAX_ARTICLES; i++) {
        card[i] = lv_obj_create(list_body);
        lv_obj_set_width(card[i], BODY_W);
        lv_obj_set_height(card[i], LV_SIZE_CONTENT);
        lv_obj_set_style_bg_opa(card[i], LV_OPA_COVER, LV_STATE_DEFAULT);
        lv_obj_set_style_border_width(card[i], 0, LV_STATE_DEFAULT);
        lv_obj_set_style_shadow_width(card[i], 0, LV_STATE_DEFAULT);
        lv_obj_set_style_radius(card[i], CARD_RADIUS, LV_STATE_DEFAULT);
        lv_obj_set_style_pad_all(card[i], CARD_PAD, LV_STATE_DEFAULT);
        lv_obj_set_style_pad_row(card[i], 6, LV_STATE_DEFAULT);
        lv_obj_set_flex_flow(card[i], LV_FLEX_FLOW_COLUMN);
        lv_obj_clear_flag(card[i], LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(card[i], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_flag(card[i], LV_OBJ_FLAG_HIDDEN);   /* shown when data lands */
        lv_obj_add_event_cb(card[i], cb_card, LV_EVENT_CLICKED, NULL);

        /* Badge + score on one row, pushed to opposite ends */
        lv_obj_t *row = lv_obj_create(card[i]);
        lv_obj_set_size(row, TEXT_W, 20);
        strip_chrome(row);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
        lv_obj_set_flex_align(row, LV_FLEX_ALIGN_SPACE_BETWEEN,
                              LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

        card_badge[i] = make_badge(row);
        card_score[i] = make_label(row, FONT_TITLE, CLR_INK, 0);

        /* Title — wraps in full, never ellipsised. Height is left as
         * LV_SIZE_CONTENT so the card grows to fit. This is the difference
         * between the list and the deck: the deck's card is a fixed size and
         * has to cut a long headline off, and this is the view you come to
         * when that cut lost the part that says what the story is. */
        card_title[i] = make_label(card[i], FONT_TITLE, CLR_INK, TEXT_W);
        lv_label_set_long_mode(card_title[i], LV_LABEL_LONG_WRAP);
        lv_obj_set_style_text_line_space(card_title[i], TITLE_LEADING, LV_STATE_DEFAULT);

        card_source[i] = make_label(card[i], FONT_META, CLR_INK, TEXT_W);
        lv_label_set_long_mode(card_source[i], LV_LABEL_LONG_DOT);
        lv_obj_set_style_text_opa(card_source[i], INK_SOURCE, LV_STATE_DEFAULT);
        lv_obj_set_style_text_letter_space(card_source[i], META_TRACKING, LV_STATE_DEFAULT);

        card_bar[i] = make_bar(card[i], 0, 0, TEXT_W);
        /* The bar sits in a flex column, so its track is placed by the layout;
         * only the fill's width is set per article in render_list(). */
        lv_obj_set_style_pad_top(lv_obj_get_parent(card_bar[i]), 0, LV_STATE_DEFAULT);
    }
}

/* ── DETAIL view ──────────────────────────────────────────────────── */
static void build_detail_view(lv_obj_t *scr)
{
    cont_detail = lv_obj_create(scr);
    lv_obj_set_size(cont_detail, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES);
    lv_obj_set_pos(cont_detail, EXAMPLE_LCD_H_RES, 0);
    strip_chrome(cont_detail);
    lv_obj_set_style_bg_opa(cont_detail, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_clear_flag(cont_detail, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(cont_detail, LV_OBJ_FLAG_HIDDEN);

    /* Back and badge share the top row, which keeps the story's own headline
     * as the first thing you read. */
    d_topbar = lv_obj_create(cont_detail);
    lv_obj_set_size(d_topbar, EXAMPLE_LCD_H_RES, HEADER_H);
    lv_obj_set_pos(d_topbar, 0, 0);
    strip_chrome(d_topbar);
    lv_obj_clear_flag(d_topbar, LV_OBJ_FLAG_SCROLLABLE);
    /* Deliberately left transparent (strip_chrome's default) rather than
     * painted in the story's tint. It is a layout row, and cont_detail behind
     * it is already that exact colour — but cont_detail is rounded during the
     * expansion and LVGL clips children to the parent's *rectangle*, not to
     * its rounded shape. An opaque bar here would square off the two top
     * corners for the whole animation. Nothing scrolls under it: detail_body
     * starts below it and is clipped to its own bounds. */

    /* Back, then a gap, then badge and score at the right. Flex rather than
     * lv_obj_align_to: aligning the badge off the score is resolved once, at
     * build time, when the score label is still empty and therefore zero wide
     * and hard against the right edge — so the badge lands *past* the panel
     * and only the last letters of it are ever on screen. A row layout
     * re-resolves every time the score text changes. */
    lv_obj_set_flex_flow(d_topbar, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(d_topbar, LV_FLEX_ALIGN_START,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_hor(d_topbar, PAD, LV_STATE_DEFAULT);
    lv_obj_set_style_pad_column(d_topbar, 6, LV_STATE_DEFAULT);

    btn_back = make_chip(d_topbar, 28, LV_SYMBOL_LEFT, &lbl_back);
    lv_obj_add_flag(btn_back, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(btn_back, cb_back, LV_EVENT_CLICKED, NULL);

    lv_obj_t *spacer = lv_obj_create(d_topbar);
    lv_obj_set_height(spacer, 1);
    lv_obj_set_flex_grow(spacer, 1);
    strip_chrome(spacer);
    lv_obj_clear_flag(spacer, LV_OBJ_FLAG_SCROLLABLE);

    lbl_d_badge = make_badge(d_topbar);
    lbl_d_score = make_label(d_topbar, FONT_TITLE, CLR_INK, 0);

    /* Scrollable story body, between the top bar and the pinned LISTEN. */
    detail_body = lv_obj_create(cont_detail);
    lv_obj_set_size(detail_body, EXAMPLE_LCD_H_RES,
                    EXAMPLE_LCD_V_RES - HEADER_H - NAV_H);
    lv_obj_set_pos(detail_body, 0, HEADER_H);
    strip_chrome(detail_body);
    lv_obj_set_style_pad_hor(detail_body, CARD_PAD, LV_STATE_DEFAULT);
    lv_obj_set_style_pad_bottom(detail_body, PAD, LV_STATE_DEFAULT);
    lv_obj_set_style_pad_row(detail_body, 8, LV_STATE_DEFAULT);
    lv_obj_set_flex_flow(detail_body, LV_FLEX_FLOW_COLUMN);
    lv_obj_add_flag(detail_body, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(detail_body, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(detail_body, LV_SCROLLBAR_MODE_AUTO);

    lbl_d_title = make_label(detail_body, FONT_DISPLAY, CLR_INK, TEXT_W);
    lv_label_set_long_mode(lbl_d_title, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_line_space(lbl_d_title, TITLE_LEADING, LV_STATE_DEFAULT);

    lbl_d_source = make_label(detail_body, FONT_META, CLR_INK, TEXT_W);
    lv_label_set_long_mode(lbl_d_source, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_opa(lbl_d_source, INK_SOURCE, LV_STATE_DEFAULT);
    lv_obj_set_style_text_letter_space(lbl_d_source, META_TRACKING, LV_STATE_DEFAULT);

    d_rule = lv_obj_create(detail_body);
    lv_obj_set_size(d_rule, TEXT_W, 1);
    strip_chrome(d_rule);
    lv_obj_set_style_bg_opa(d_rule, INK_HAIRLINE, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(d_rule, CLR_INK, LV_STATE_DEFAULT);
    lv_obj_clear_flag(d_rule, LV_OBJ_FLAG_SCROLLABLE);

    lbl_d_summary = make_label(detail_body, FONT_BODY, CLR_INK, TEXT_W);
    lv_label_set_long_mode(lbl_d_summary, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_opa(lbl_d_summary, INK_BODY, LV_STATE_DEFAULT);
    lv_obj_set_style_text_line_space(lbl_d_summary, BODY_LEADING, LV_STATE_DEFAULT);

    /* LISTEN / STOP — pinned to the bottom rather than sitting in the scroll,
     * so it is reachable without paging to the end of an 800-word summary. */
    btn_listen = make_button(cont_detail, EXAMPLE_LCD_H_RES, NAV_H,
                             CLR_INK, CLR_RULE, cb_listen);
    lv_obj_set_pos(btn_listen, 0, EXAMPLE_LCD_V_RES - NAV_H);

    lbl_listen = make_label(btn_listen, FONT_TITLE, CLR_ACCENT, 0);
    lv_obj_set_style_text_letter_space(lbl_listen, META_TRACKING, LV_STATE_DEFAULT);
    lv_label_set_text(lbl_listen, LV_SYMBOL_PLAY "  LISTEN");
    lv_obj_align(lbl_listen, LV_ALIGN_CENTER, 0, 0);
}

/* ── Entry point — called from lvgl_port_init() under the LVGL lock ── */
void news_ui_create(void)
{
    pinMode(BOOT_BUTTON_GPIO, INPUT_PULLUP);

    lv_obj_t *scr = lv_scr_act();
    lv_obj_set_style_bg_color(scr, CLR_BG, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(scr, 0, LV_STATE_DEFAULT);

    /* Build order is z-order, and it is load-bearing. The list rides up over
     * the deck and the detail slides in over both, so they have to be created
     * in that order — building the list first put it *underneath* an opaque
     * full-screen deck, where it animated into place perfectly and was never
     * visible, which looks exactly like the swipe not being detected at all.
     *
     * Fixed at build time rather than with lv_obj_move_foreground() on each
     * transition, so the child indices stay constant: sim_main.c's --scroll
     * reaches list_body as child 2 of child 1 of the screen. */
    build_deck_view(scr);
    build_list_view(scr);
    build_detail_view(scr);

    deck_settle();

    /* Gestures are caught at the screen. Children bubble them up, so this one
     * handler covers every view and everything inside them. */
    lv_obj_add_event_cb(scr, cb_gesture, LV_EVENT_GESTURE, NULL);

    /* The shells have to be clickable or a swipe that starts on bare
     * background does nothing at all. LVGL hit-tests only CLICKABLE objects to
     * decide which one the press belongs to, and it sends the gesture to that
     * object — so with a non-clickable background there is no target, no
     * event, and nothing to bubble. Neither has a CLICKED handler; being
     * clickable here only means "presses land on me". */
    lv_obj_add_flag(cont_deck, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(cont_list, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_add_flag(cont_deck, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_flag(cont_list, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_flag(list_body, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_flag(cont_detail, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_flag(detail_body, LV_OBJ_FLAG_GESTURE_BUBBLE);
    for (int f = 0; f < 2; f++) lv_obj_add_flag(face[f], LV_OBJ_FLAG_GESTURE_BUBBLE);

    lv_timer_create(ui_timer_cb, 250, NULL);

#ifdef ESP_PLATFORM
    /* Its own timer at 100 ms rather than folded into the 250 ms one: picking
     * the panel up should light it before you have finished lifting it, and a
     * quarter-second lag between the movement and the response reads as the
     * board being slow rather than as a considered fade. Still on the LVGL
     * task, so it is serialised with everything else. */
    imu_wake_init();
    lv_timer_create([](lv_timer_t *t) { (void)t; imu_wake_poll(); }, 100, NULL);
#endif
}
