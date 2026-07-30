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
#define NEWS_SUMMARY_LEN    420
#define NEWS_SOURCE_LEN     28
#define NEWS_AREA_LEN       24

#define NEWS_REFRESH_INTERVAL_MS  (15 * 60 * 1000)   /* 15 min */

/* Backend root, shared by news_client (digest) and news_audio (narration) so
 * the host and port are written once. Empty string = run on sample data with
 * no network. Port 8010, not 8000 — Docker holds 8000 on the Mac. The address
 * is DHCP and will move; reserve it on the router for unattended use. */
#define NEWS_BASE_URL  "http://192.168.1.171:8010"

typedef struct {
    char  title[NEWS_TITLE_LEN];
    char  summary[NEWS_SUMMARY_LEN];
    char  source[NEWS_SOURCE_LEN];
    char  area[NEWS_AREA_LEN];    /* matched_area from the score node */
    float score;
} news_article_t;

/* Written only by news_task, read by the LVGL UI task. */
extern news_article_t    news_articles[NEWS_MAX_ARTICLES];
extern volatile int      news_count;
extern volatile uint32_t news_data_version;  /* bumped last, after a good load */
extern volatile bool     news_fetch_failed;  /* last HTTP attempt failed */

void news_client_init(void);

/* Ask for an out-of-cycle refresh (set from a UI callback; the task clears it). */
void news_client_request_refresh(void);

#ifdef __cplusplus
}
#endif
