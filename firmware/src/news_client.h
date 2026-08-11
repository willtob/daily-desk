/*
 * news_client.h — fetches the scored news digest over HTTP.
 *
 * Follows the notion_client.h pattern: fixed-size static buffers (no heap
 * churn on a microcontroller), a volatile count, and a version counter the UI
 * watches to know when to re-render.
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Keep MAX modest — every article costs LVGL widgets out of the 48 KB
 * LV_MEM_SIZE heap, and 12 is already more than you'd scroll on a phone. */
#define NEWS_MAX_ARTICLES   12
#define NEWS_TITLE_LEN      112
/* 960, not 420: Phase 9 replaced the RSS blurb with an LLM summary written to
 * an ~800-character target, and the backend's digest_payload trims to 900. A
 * 420-byte buffer would cut those back into the teasers the phase set out to
 * get rid of. Costs 12 x 960 = 11.5 KB of static RAM, which is nothing against
 * 8 MB of PSRAM — the detail body already scrolls, so nothing else changes. */
#define NEWS_SUMMARY_LEN    960
#define NEWS_SOURCE_LEN     28
#define NEWS_AREA_LEN       24

#define NEWS_REFRESH_INTERVAL_MS  (15 * 60 * 1000)   /* 15 min */

/* Backend root, shared by news_client (digest) and news_audio (narration) so
 * the host and port are written once. Empty string = run on sample data with
 * no network. Port 8010, not 8000 — Docker holds 8000 on the Mac. The address
 * is DHCP and will move; reserve it on the router for unattended use. */
#define NEWS_BASE_URL  "http://192.168.1.187:8010"

typedef struct {
    char  title[NEWS_TITLE_LEN];
    char  summary[NEWS_SUMMARY_LEN];
    char  source[NEWS_SOURCE_LEN];
    char  area[NEWS_AREA_LEN];    /* matched_area from the score node */
    float score;
    /* The exploration slot: the backend appends one article picked *because*
     * it scored badly. Always last in the payload, and styled as its own thing
     * rather than as its area — the area is only the closest the profile got,
     * so tinting the card by it would be a lie the size of the whole card. */
    bool  wildcard;
} news_article_t;

/* Written only by news_task, read by the LVGL UI task. */
extern news_article_t    news_articles[NEWS_MAX_ARTICLES];
extern volatile int      news_count;
extern volatile uint32_t news_data_version;  /* bumped last, after a good load */
extern volatile bool     news_fetch_failed;  /* last HTTP attempt failed */
extern volatile bool     news_rebuilding;    /* backend is re-running the pipeline */

void news_client_init(void);

/* Two different things, both called "refresh" in casual speech:
 *
 *   request_refresh   re-GET digest.json. Cheap (~100 ms), but the backend only
 *                     rewrites that file when the pipeline runs, so between runs
 *                     it returns exactly what is already on screen.
 *   request_rebuild   POST /refresh, wait for the pipeline to finish, then
 *                     re-GET. This is the one that pulls new RSS content, and it
 *                     takes 20-30 s because feeds and embeddings are slow.
 *
 * Both are set from a UI callback and cleared by news_task; the UI never blocks
 * on either. */
void news_client_request_refresh(void);
void news_client_request_rebuild(void);

#ifdef __cplusplus
}
#endif
