/*
 * news_ui.cpp — two-view news reader for the 172 x 640 portrait panel.
 *
 *   LIST view    scrollable column of article cards, best-scoring first.
 *                Tap a card to open it.
 *   DETAIL view  full title, source, score and the scrollable summary.
 *                Tap BACK (or press BOOT) to return to the list.
 *
 * Both views are built once in news_ui_create() and swapped by toggling
 * LV_OBJ_FLAG_HIDDEN, so no widget is ever created outside the LVGL lock.
 * All refreshes happen in ui_timer_cb, which LVGL calls under the lock —
 * the same pattern pomodoro_ui.cpp uses.
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
#include "lvgl.h"
#include "user_config.h"
#include <stdio.h>
#include <string.h>

/* ── Palette — same family as the pomodoro timer ──────────────────── */
#define CLR_BG            lv_color_hex(0x1A1A2E)
#define CLR_CARD          lv_color_hex(0x232342)
#define CLR_CARD_PRESSED  lv_color_hex(0x32325C)
#define CLR_WHITE         lv_color_hex(0xFFFFFF)
#define CLR_DIM           lv_color_hex(0x8A8AA3)
#define CLR_RULE          lv_color_hex(0x32325C)
#define CLR_ACCENT        lv_color_hex(0xE94560)

/* ── Layout ───────────────────────────────────────────────────────── */
#define PAD          6
#define HEADER_H     44
#define BODY_W       (EXAMPLE_LCD_H_RES - 2 * PAD)   /* 160 px */
#define CARD_PAD     8
#define TEXT_W       (BODY_W - 2 * CARD_PAD)         /* 144 px */
#define TITLE_LINES  3
#define TITLE_H      (TITLE_LINES * 20)              /* montserrat_16 ~20 px/line */
#define BAR_H        3

/* Cosine scores from the fitness function realistically land in this band;
 * the score bar maps it to 0..TEXT_W so differences are actually visible. */
#define SCORE_MIN    0.25f
#define SCORE_MAX    0.60f

/* ── Widget handles ───────────────────────────────────────────────── */
static lv_obj_t *cont_list;
static lv_obj_t *lbl_status;
static lv_obj_t *list_body;

static lv_obj_t *card[NEWS_MAX_ARTICLES];
static lv_obj_t *card_badge[NEWS_MAX_ARTICLES];
static lv_obj_t *card_score[NEWS_MAX_ARTICLES];
static lv_obj_t *card_title[NEWS_MAX_ARTICLES];
static lv_obj_t *card_source[NEWS_MAX_ARTICLES];
static lv_obj_t *card_bar[NEWS_MAX_ARTICLES];

static lv_obj_t *cont_detail;
static lv_obj_t *detail_body;
static lv_obj_t *lbl_d_badge;
static lv_obj_t *lbl_d_title;
static lv_obj_t *lbl_d_meta;
static lv_obj_t *lbl_d_summary;
static lv_obj_t *btn_listen;
static lv_obj_t *lbl_listen;

/* ── View state ───────────────────────────────────────────────────── */
static int      selected     = -1;
static bool     detail_open  = false;
static uint32_t last_version = 0;

/* ── Interest areas → colour + short badge text ───────────────────
 * The names must match `matched_area` from the Python score node. The badge
 * text is abbreviated because 144 px is not enough for "classic_ml_applied". */
typedef struct {
    const char *area;
    const char *label;
    uint32_t    color;
} area_style_t;

static const area_style_t AREA_STYLES[] = {
    { "ai_open_source",     "OPEN SRC", 0x4CAF50 },
    { "ai_consciousness",   "INTERP",   0xA97BF7 },
    { "classic_ml_applied", "CLASSIC",  0x26C6DA },
    { "big_tech_career",    "BIG TECH", 0x42A5F5 },
    { "embedded_wearables", "EMBEDDED", 0xFF9800 },
    { "startup_vc",         "STARTUP",  0xFFD54F },
    { "florida",            "FLORIDA",  0xE94560 },
    { "spain",              "SPAIN",    0xF06292 },
};
#define AREA_STYLE_COUNT (sizeof(AREA_STYLES) / sizeof(AREA_STYLES[0]))

static const area_style_t *area_style(const char *area)
{
    static const area_style_t fallback = { "", "NEWS", 0x8A8AA3 };
    if (area) {
        for (unsigned i = 0; i < AREA_STYLE_COUNT; i++) {
            if (strcmp(area, AREA_STYLES[i].area) == 0) return &AREA_STYLES[i];
        }
    }
    return &fallback;
}

/* Score → bar width in pixels, clamped to the visible band. */
static lv_coord_t score_bar_w(float score)
{
    float t = (score - SCORE_MIN) / (SCORE_MAX - SCORE_MIN);
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;
    lv_coord_t w = (lv_coord_t)(t * TEXT_W);
    return w < 2 ? 2 : w;   /* always show a sliver so the bar reads as a bar */
}

/* ── Slide transition ─────────────────────────────────────────────────
 *
 * Detail slides in over the list from the right and back out the same way,
 * which is what makes the swipe feel like it's moving a sheet of paper rather
 * than cutting to a new screen. The list stays visible underneath for the
 * duration — cont_detail is opaque, so nothing shows through — and is only
 * hidden once the animation lands, to keep the redraw cost off the panel. */
#define SLIDE_MS  180

static void anim_x_cb(void *obj, int32_t v)
{
    lv_obj_set_x((lv_obj_t *)obj, (lv_coord_t)v);
}

static void anim_hide_ready_cb(lv_anim_t *a)
{
    lv_obj_add_flag((lv_obj_t *)a->var, LV_OBJ_FLAG_HIDDEN);
}

static void slide(lv_obj_t *obj, lv_coord_t from, lv_coord_t to, bool hide_after)
{
    lv_anim_del(obj, anim_x_cb);      /* a fast double-swipe must not stack */

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, obj);
    lv_anim_set_exec_cb(&a, anim_x_cb);
    lv_anim_set_values(&a, from, to);
    lv_anim_set_time(&a, SLIDE_MS);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    if (hide_after) lv_anim_set_ready_cb(&a, anim_hide_ready_cb);
    lv_anim_start(&a);
}

/* ── View switching ───────────────────────────────────────────────── */
static void show_list(void)
{
    detail_open = false;
    lv_obj_clear_flag(cont_list, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_x(cont_list, 0);
    slide(cont_detail, 0, EXAMPLE_LCD_H_RES, true);
}

static void show_detail(int idx)
{
    if (idx < 0 || idx >= news_count) return;
    selected = idx;

    const news_article_t *a = &news_articles[idx];
    const area_style_t   *s = area_style(a->area);

    lv_label_set_text(lbl_d_badge, s->label);
    lv_obj_set_style_text_color(lbl_d_badge, lv_color_hex(s->color), LV_STATE_DEFAULT);

    lv_label_set_text(lbl_d_title, a->title);

    char meta[64];
    snprintf(meta, sizeof(meta), "%s  |  %.3f", a->source, a->score);
    lv_label_set_text(lbl_d_meta, meta);

    lv_label_set_text(lbl_d_summary,
                      a->summary[0] ? a->summary : "(no summary in the feed)");

    /* Always open a story at the top, regardless of where the last one was left. */
    lv_obj_scroll_to_y(detail_body, 0, LV_ANIM_OFF);

    detail_open = true;
    lv_obj_clear_flag(cont_detail, LV_OBJ_FLAG_HIDDEN);
    /* List stays visible under the incoming sheet; hiding it here would show
     * the bare screen background through the gap during the slide. */
    slide(cont_detail, EXAMPLE_LCD_H_RES, 0, false);
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
    int idx = card_index_of(lv_event_get_target(e));
    if (idx >= 0) show_detail(idx);
}

static void cb_back(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    /* Leaving a story stops its narration — otherwise audio keeps playing
     * over a list you're already scrolling through. */
    if (news_audio_playing) news_audio_stop();
    show_list();
}

/* Horizontal swipes. Both scrollable bodies scroll vertically, so left/right
 * is free and LVGL only reports a gesture when the press didn't become a
 * scroll — the two can't fight each other. */
static void cb_gesture(lv_event_t *e)
{
    (void)e;
    if (!detail_open) return;      /* the list only scrolls */

    lv_dir_t dir = lv_indev_get_gesture_dir(lv_indev_get_act());

    if (dir == LV_DIR_RIGHT) {                       /* back, iOS-style */
        if (news_audio_playing) news_audio_stop();
        show_list();
    } else if (dir == LV_DIR_LEFT) {                 /* next story */
        if (selected + 1 < news_count) {
            if (news_audio_playing) news_audio_stop();
            show_detail(selected + 1);
        }
    }
}

static void cb_listen(lv_event_t *e)
{
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    /* Both branches only set a flag; audio_task does the work. */
    if (news_audio_playing) news_audio_stop();
    else if (selected >= 0)  news_audio_play(selected);
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

        lv_label_set_text(card_badge[i], s->label);
        lv_obj_set_style_text_color(card_badge[i], c, LV_STATE_DEFAULT);

        char sbuf[12];
        snprintf(sbuf, sizeof(sbuf), "%.2f", a->score);
        lv_label_set_text(card_score[i], sbuf);

        lv_label_set_text(card_title[i], a->title);
        lv_label_set_text(card_source[i], a->source);

        lv_obj_set_width(card_bar[i], score_bar_w(a->score));
        lv_obj_set_style_bg_color(card_bar[i], c, LV_STATE_DEFAULT);

        lv_obj_clear_flag(card[i], LV_OBJ_FLAG_HIDDEN);
    }

    lv_obj_scroll_to_y(list_body, 0, LV_ANIM_OFF);
}

static void render_status(void)
{
    char buf[32];

    if (news_count > 0) {
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

/* ── BOOT button: back out of a story, or force a refresh on the list ── */
static void poll_boot_button(void)
{
    static bool was_down = false;
    bool down = (digitalRead(BOOT_BUTTON_GPIO) == LOW);

    if (down && !was_down) {           /* 250 ms polling is its own debounce */
        if (detail_open) show_list();
        else             news_client_request_refresh();
    }
    was_down = down;
}

static void ui_timer_cb(lv_timer_t *t)
{
    (void)t;

    if (news_data_version != last_version) {
        last_version = news_data_version;
        render_list();
        /* If an open story vanished from a smaller refresh, fall back to list. */
        if (detail_open && selected >= news_count) show_list();
    }

    render_status();
    poll_boot_button();

    /* Track playback state so the button says what it will do next. */
    if (detail_open) {
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

/* ── LIST view ────────────────────────────────────────────────────── */
static void build_list_view(lv_obj_t *scr)
{
    cont_list = lv_obj_create(scr);
    lv_obj_set_size(cont_list, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES);
    lv_obj_set_pos(cont_list, 0, 0);
    strip_chrome(cont_list);
    lv_obj_clear_flag(cont_list, LV_OBJ_FLAG_SCROLLABLE);

    /* Header — fixed, does not scroll with the list */
    lv_obj_t *hdr = lv_obj_create(cont_list);
    lv_obj_set_size(hdr, EXAMPLE_LCD_H_RES, HEADER_H);
    lv_obj_set_pos(hdr, 0, 0);
    strip_chrome(hdr);
    lv_obj_clear_flag(hdr, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *lbl_ttl = make_label(hdr, &lv_font_montserrat_20, CLR_WHITE, 0);
    lv_label_set_text(lbl_ttl, "NEWS");
    lv_obj_align(lbl_ttl, LV_ALIGN_LEFT_MID, PAD, 0);

    lbl_status = make_label(hdr, &lv_font_montserrat_14, CLR_DIM, 0);
    lv_label_set_text(lbl_status, "loading...");
    lv_obj_align(lbl_status, LV_ALIGN_RIGHT_MID, -PAD, 0);

    /* Hairline under the header */
    lv_obj_t *rule = lv_obj_create(cont_list);
    lv_obj_set_size(rule, EXAMPLE_LCD_H_RES, 1);
    lv_obj_set_pos(rule, 0, HEADER_H);
    strip_chrome(rule);
    lv_obj_set_style_bg_opa(rule, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(rule, CLR_RULE, LV_STATE_DEFAULT);

    /* Scrollable card column */
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
        lv_obj_set_style_bg_color(card[i], CLR_CARD, LV_STATE_DEFAULT);
        lv_obj_set_style_bg_color(card[i], CLR_CARD_PRESSED, LV_STATE_PRESSED);
        lv_obj_set_style_border_width(card[i], 0, LV_STATE_DEFAULT);
        lv_obj_set_style_shadow_width(card[i], 0, LV_STATE_DEFAULT);
        lv_obj_set_style_radius(card[i], 10, LV_STATE_DEFAULT);
        lv_obj_set_style_pad_all(card[i], CARD_PAD, LV_STATE_DEFAULT);
        lv_obj_set_style_pad_row(card[i], 4, LV_STATE_DEFAULT);
        lv_obj_set_flex_flow(card[i], LV_FLEX_FLOW_COLUMN);
        lv_obj_clear_flag(card[i], LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(card[i], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_flag(card[i], LV_OBJ_FLAG_HIDDEN);   /* shown when data lands */
        lv_obj_add_event_cb(card[i], cb_card, LV_EVENT_CLICKED, NULL);

        /* Badge + score on one row, pushed to opposite ends */
        lv_obj_t *row = lv_obj_create(card[i]);
        lv_obj_set_size(row, TEXT_W, 16);
        strip_chrome(row);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
        lv_obj_set_flex_align(row, LV_FLEX_ALIGN_SPACE_BETWEEN,
                              LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

        card_badge[i] = make_label(row, &lv_font_montserrat_14, CLR_DIM, 0);
        card_score[i] = make_label(row, &lv_font_montserrat_14, CLR_DIM, 0);

        /* Title — wraps in full, never ellipsised. Height is left as
         * LV_SIZE_CONTENT so the card grows to fit; at 144 px a headline runs
         * to 5-7 lines and clipping it at 3 hid the part that says what the
         * story actually is, which is the whole job of this screen. */
        card_title[i] = make_label(card[i], &lv_font_montserrat_16, CLR_WHITE, TEXT_W);
        lv_label_set_long_mode(card_title[i], LV_LABEL_LONG_WRAP);

        card_source[i] = make_label(card[i], &lv_font_montserrat_14, CLR_DIM, TEXT_W);
        lv_label_set_long_mode(card_source[i], LV_LABEL_LONG_DOT);

        /* Score bar — width set per article in render_list() */
        card_bar[i] = lv_obj_create(card[i]);
        lv_obj_set_size(card_bar[i], 2, BAR_H);
        strip_chrome(card_bar[i]);
        lv_obj_set_style_bg_opa(card_bar[i], LV_OPA_COVER, LV_STATE_DEFAULT);
        lv_obj_set_style_radius(card_bar[i], BAR_H / 2, LV_STATE_DEFAULT);
        lv_obj_clear_flag(card_bar[i], LV_OBJ_FLAG_SCROLLABLE);
    }
}

/* ── DETAIL view ──────────────────────────────────────────────────── */
static void build_detail_view(lv_obj_t *scr)
{
    cont_detail = lv_obj_create(scr);
    lv_obj_set_size(cont_detail, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES);
    lv_obj_set_pos(cont_detail, 0, 0);
    strip_chrome(cont_detail);
    lv_obj_set_style_bg_opa(cont_detail, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(cont_detail, CLR_BG, LV_STATE_DEFAULT);
    lv_obj_clear_flag(cont_detail, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(cont_detail, LV_OBJ_FLAG_HIDDEN);

    /* Back button — full width so it's an easy target on a 172 px panel */
    lv_obj_t *btn_back = lv_btn_create(cont_detail);
    lv_obj_set_size(btn_back, BODY_W, HEADER_H - 6);
    lv_obj_set_pos(btn_back, PAD, 4);
    lv_obj_set_style_bg_color(btn_back, CLR_CARD, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(btn_back, CLR_CARD_PRESSED, LV_STATE_PRESSED);
    lv_obj_set_style_border_width(btn_back, 0, LV_STATE_DEFAULT);
    lv_obj_set_style_shadow_width(btn_back, 0, LV_STATE_DEFAULT);
    lv_obj_set_style_radius(btn_back, 10, LV_STATE_DEFAULT);
    lv_obj_add_event_cb(btn_back, cb_back, LV_EVENT_CLICKED, NULL);

    lv_obj_t *lbl_back = make_label(btn_back, &lv_font_montserrat_16, CLR_ACCENT, 0);
    lv_label_set_text(lbl_back, LV_SYMBOL_LEFT "  BACK");
    lv_obj_align(lbl_back, LV_ALIGN_CENTER, 0, 0);

    /* Scrollable story body */
    detail_body = lv_obj_create(cont_detail);
    lv_obj_set_size(detail_body, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES - HEADER_H - 4);
    lv_obj_set_pos(detail_body, 0, HEADER_H + 4);
    strip_chrome(detail_body);
    lv_obj_set_style_pad_all(detail_body, PAD, LV_STATE_DEFAULT);
    lv_obj_set_style_pad_row(detail_body, 8, LV_STATE_DEFAULT);
    lv_obj_set_flex_flow(detail_body, LV_FLEX_FLOW_COLUMN);
    lv_obj_add_flag(detail_body, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(detail_body, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(detail_body, LV_SCROLLBAR_MODE_AUTO);

    lbl_d_badge = make_label(detail_body, &lv_font_montserrat_14, CLR_DIM, TEXT_W);

    lbl_d_title = make_label(detail_body, &lv_font_montserrat_20, CLR_WHITE, TEXT_W);
    lv_label_set_long_mode(lbl_d_title, LV_LABEL_LONG_WRAP);

    lbl_d_meta = make_label(detail_body, &lv_font_montserrat_14, CLR_DIM, TEXT_W);
    lv_label_set_long_mode(lbl_d_meta, LV_LABEL_LONG_WRAP);

    lv_obj_t *rule = lv_obj_create(detail_body);
    lv_obj_set_size(rule, TEXT_W, 1);
    strip_chrome(rule);
    lv_obj_set_style_bg_opa(rule, LV_OPA_COVER, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(rule, CLR_RULE, LV_STATE_DEFAULT);

    /* LISTEN / STOP — above the summary so it's reachable without scrolling
     * to the bottom of a long story. */
    btn_listen = lv_btn_create(detail_body);
    lv_obj_set_size(btn_listen, TEXT_W, 40);
    lv_obj_set_style_bg_color(btn_listen, CLR_CARD, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_color(btn_listen, CLR_CARD_PRESSED, LV_STATE_PRESSED);
    lv_obj_set_style_border_width(btn_listen, 1, LV_STATE_DEFAULT);
    lv_obj_set_style_border_color(btn_listen, CLR_ACCENT, LV_STATE_DEFAULT);
    lv_obj_set_style_shadow_width(btn_listen, 0, LV_STATE_DEFAULT);
    lv_obj_set_style_radius(btn_listen, 10, LV_STATE_DEFAULT);
    lv_obj_add_event_cb(btn_listen, cb_listen, LV_EVENT_CLICKED, NULL);

    lbl_listen = make_label(btn_listen, &lv_font_montserrat_16, CLR_ACCENT, 0);
    lv_label_set_text(lbl_listen, LV_SYMBOL_PLAY "  LISTEN");
    lv_obj_align(lbl_listen, LV_ALIGN_CENTER, 0, 0);

    lbl_d_summary = make_label(detail_body, &lv_font_montserrat_14, CLR_WHITE, TEXT_W);
    lv_label_set_long_mode(lbl_d_summary, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_line_space(lbl_d_summary, 3, LV_STATE_DEFAULT);
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

    build_list_view(scr);
    build_detail_view(scr);

    /* Gestures are caught at the screen. Children bubble them up, so this one
     * handler covers the detail body and everything inside it. */
    lv_obj_add_event_cb(scr, cb_gesture, LV_EVENT_GESTURE, NULL);
    lv_obj_add_flag(cont_detail, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_flag(detail_body, LV_OBJ_FLAG_GESTURE_BUBBLE);

    lv_timer_create(ui_timer_cb, 250, NULL);
}
