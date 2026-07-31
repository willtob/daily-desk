/*
 * sim_stubs.c — host-side implementations of the firmware modules the UI talks
 * to, using the REAL headers so the simulator can't drift from the device API.
 *
 * The UI is the thing under test here; networking, audio and Wi-Fi are not.
 */
#include <string.h>
#include <stdio.h>
#include "news_client.h"
#include "news_audio.h"
#include "wifi_manager.h"

/* ── wifi_manager ──────────────────────────────────────────────────────── */
volatile wifi_status_t wifi_conn_status = WIFI_CONNECTED;
char wifi_ip_str[20] = "192.168.1.74";
void wifi_manager_init(void) {}

/* ── news_audio ────────────────────────────────────────────────────────── */
volatile bool news_audio_playing = false;
volatile int  news_audio_index   = -1;
volatile bool news_audio_failed  = false;

void news_audio_init(void) {}
void news_audio_play(int i)
{
    /* Toggle state so the LISTEN/STOP button can be exercised visually. */
    news_audio_index   = i;
    news_audio_playing = true;
    printf("[sim] play article %d\n", i);
}
void news_audio_stop(void)
{
    news_audio_playing = false;
    news_audio_index   = -1;
    printf("[sim] stop\n");
}

/* ── news_client ───────────────────────────────────────────────────────── */
news_article_t    news_articles[NEWS_MAX_ARTICLES];
volatile int      news_count         = 0;
volatile uint32_t news_data_version  = 0;
volatile bool     news_fetch_failed  = false;

void news_client_request_refresh(void) {}

/* Representative content: long and short titles, every interest area, an
 * accented Spanish entry, and one summary-less article — the cases that
 * actually break layout. */
void news_client_init(void)
{
    struct { const char *title, *source, *area; float score; const char *summary; } s[] = {
        { "Spain's three worst wildfires since 1961 happened in last two years",
          "The Local Spain", "spain", 0.6103f,
          "The wildfire that engulfed Spain's Avila region, northwest of the capital "
          "Madrid, is now considered the largest in the country's modern history, with "
          "around 50,000 hectares burned, the government said this week." },
        { "Dili raises $21.7M to bring AI compliance to the infrastructure boom",
          "TechCrunch", "startup_vc", 0.5982f,
          "The Series A was led by Khosla Ventures, with participation from Allianz, "
          "Rebel Fund, and Y Combinator's Garry Tan." },
        { "El inicio de la larga ola de calor trae de nuevo incendios forestales en Catalunya",
          "La Vanguardia Barcelona", "spain", 0.5935f,
          "El fuego confina varias horas 3.600 vecinos en el Baix Penedes y en Canyelles, "
          "en el Garraf." },
        { "The lineage behind 69% of open models was never verified. Cisco just "
          "fingerprinted almost 900 for free",
          "VentureBeat", "ai_open_source", 0.5648f,
          "A security team approving an open-source model for production today starts "
          "with a repository page. The tag identifying the base model it descended from "
          "is a string the uploader typed." },
        { "Running a 28.9M parameter LLM on an $8 microcontroller",
          "Hackaday", "classic_ml_applied", 0.5220f,
          "A demonstration of quantized inference on constrained hardware, with the "
          "full memory budget broken down layer by layer." },
        { "AI #179 Part 1: A Louder Fire Alarm for General Intelligence",
          "LessWrong", "ai_consciousness", 0.5445f,
          "Coverage of the week's model releases, the system card, and the model "
          "welfare discussion that followed." },
        { "Short title", "NBC6 Miami", "florida", 0.4400f, "" },
        { "Meta engineering on how the build system scales to a monorepo",
          "Meta Engineering", "big_tech_career", 0.4310f,
          "An account of incremental build graphs and the caching layer underneath." },
        { "New low power microcontroller with e-ink support and a tiny footprint",
          "CNX Software", "embedded_wearables", 0.4120f,
          "Datasheet highlights, power figures in deep sleep, and the dev board price." },
        { "Unknown area falls back to a grey badge", "Some Feed", "not_a_real_area",
          0.3900f, "Checks that an area added to interests.yaml but not to the "
          "firmware's AREA_STYLES table still renders sensibly." },
    };

    int n = (int)(sizeof(s) / sizeof(s[0]));
    if (n > NEWS_MAX_ARTICLES) n = NEWS_MAX_ARTICLES;
    for (int i = 0; i < n; i++) {
        strncpy(news_articles[i].title,   s[i].title,   NEWS_TITLE_LEN - 1);
        strncpy(news_articles[i].summary, s[i].summary, NEWS_SUMMARY_LEN - 1);
        strncpy(news_articles[i].source,  s[i].source,  NEWS_SOURCE_LEN - 1);
        strncpy(news_articles[i].area,    s[i].area,    NEWS_AREA_LEN - 1);
        news_articles[i].score = s[i].score;
    }
    news_count = n;
    news_data_version = 1;
    printf("[sim] loaded %d sample articles\n", n);
}
