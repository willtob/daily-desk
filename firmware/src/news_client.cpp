/*
 * news_client.cpp — polls the esp-news-reporter digest endpoint.
 *
 * Expected JSON (this is the contract Phase 5/6 of esp-news-reporter must
 * serve — the field names match the Python Article model exactly so the
 * backend can dump curated articles with no renaming):
 *
 *   {
 *     "articles": [
 *       { "title": "...", "summary": "...", "source": "Hackaday",
 *         "matched_area": "ai_open_source", "score": 0.4213 },
 *       ...
 *     ]
 *   }
 *
 * Until that endpoint exists, load_sample_data() fills the buffers so the UI
 * is fully usable on-device. Set NEWS_URL and the sample data is ignored.
 */
#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include "news_client.h"
#include "wifi_manager.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* ── Endpoint ────────────────────────────────────────────────────────────
 * Point this at the Phase 6 FastAPI host on your LAN, e.g.
 *   #define NEWS_URL "http://192.168.1.42:8000/digest.json"
 * Leave empty to run on the built-in sample data.
 * (Plain http:// only — an https:// endpoint needs WiFiClientSecure here.) */
/* Derived from NEWS_BASE_URL in news_client.h, shared with news_audio.cpp. */
#define NEWS_URL  NEWS_BASE_URL "/digest.json"

#define HTTP_TIMEOUT_MS  8000

news_article_t    news_articles[NEWS_MAX_ARTICLES];
volatile int      news_count         = 0;
volatile uint32_t news_data_version  = 0;
volatile bool     news_fetch_failed  = false;

static volatile bool refresh_req = false;

void news_client_request_refresh(void)
{
    refresh_req = true;
}

/* Copy a JSON string field into a fixed buffer, always NUL-terminated. */
static void copy_field(char *dst, size_t dst_len, const char *src)
{
    if (!src) { dst[0] = '\0'; return; }
    strlcpy(dst, src, dst_len);
}

/* Placeholder digest so the UI can be developed and demoed before the
 * backend exists. Deliberately generic — not real headlines. */
static void load_sample_data(void)
{
    struct {
        const char *title, *source, *area;
        float       score;
        const char *summary;
    } samples[] = {
        { "Sample: open weight model release with permissive license",
          "Hugging Face", "ai_open_source", 0.5120f,
          "Placeholder summary. This entry exists so the list and detail views "
          "can be exercised on-device before the digest endpoint is running. "
          "Scroll this text to check that long summaries wrap and scroll "
          "correctly inside the 172 px wide panel." },
        { "Sample: steering vectors found in a mid-size transformer",
          "Alignment Forum", "ai_consciousness", 0.4874f,
          "Placeholder summary for the interpretability area. Replace by "
          "pointing NEWS_URL at the Phase 6 endpoint." },
        { "Sample: flash flood warning issued for Broward County",
          "NWS Florida", "florida", 0.4610f,
          "Placeholder summary for the Florida alert path — the case where you "
          "want the device to be glanceable and immediate." },
        { "Sample: obres de la L9 generen retards a Barcelona",
          "El Periodico", "spain", 0.4402f,
          "Placeholder summary in the shape of a Spanish-language Barcelona "
          "story, to check accented characters render." },
        { "Sample: CNN deployed for defect detection on a factory line",
          "NVIDIA Developer", "classic_ml_applied", 0.4155f,
          "Placeholder summary for the applied classic-architecture area." },
        { "Sample: engineering blog on how a large team runs its build system",
          "Meta Engineering", "big_tech_career", 0.3981f,
          "Placeholder summary for the big-tech career area." },
        { "Sample: new low power microcontroller with e-ink support",
          "Hackaday", "embedded_wearables", 0.3760f,
          "Placeholder summary for the embedded area." },
        { "Sample: hardware startup raises a Series A",
          "Crunchbase News", "startup_vc", 0.3542f,
          "Placeholder summary for the startup/VC area." },
    };

    int n = sizeof(samples) / sizeof(samples[0]);
    if (n > NEWS_MAX_ARTICLES) n = NEWS_MAX_ARTICLES;

    for (int i = 0; i < n; i++) {
        copy_field(news_articles[i].title,   NEWS_TITLE_LEN,   samples[i].title);
        copy_field(news_articles[i].summary, NEWS_SUMMARY_LEN, samples[i].summary);
        copy_field(news_articles[i].source,  NEWS_SOURCE_LEN,  samples[i].source);
        copy_field(news_articles[i].area,    NEWS_AREA_LEN,    samples[i].area);
        news_articles[i].score = samples[i].score;
    }

    news_count = n;
    /* Bumped last — the UI renders only after count/data are in place. Written
     * as an assignment, not ++, because ++ on a volatile is deprecated in C++20. */
    news_data_version = news_data_version + 1;
    Serial.printf("[News] Loaded %d sample articles (NEWS_URL is empty)\n", n);
}

/* One HTTP fetch + parse. Returns true only if articles were loaded. */
static bool news_refresh_once(void)
{
    if (wifi_conn_status != WIFI_CONNECTED) {
        Serial.println("[News] Skipping fetch — Wi-Fi not connected");
        return false;
    }

    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    if (!http.begin(NEWS_URL)) {
        Serial.println("[News] http.begin() failed");
        return false;
    }

    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        Serial.printf("[News] HTTP %d\n", code);
        http.end();
        return false;
    }

    /* Parse straight off the stream so the payload isn't buffered twice. */
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, http.getStream());
    http.end();

    if (err) {
        Serial.printf("[News] JSON parse failed: %s\n", err.c_str());
        return false;
    }

    JsonArray arr = doc["articles"].as<JsonArray>();
    if (arr.isNull()) {
        Serial.println("[News] Response has no 'articles' array");
        return false;
    }

    int i = 0;
    for (JsonObject a : arr) {
        if (i >= NEWS_MAX_ARTICLES) break;
        copy_field(news_articles[i].title,   NEWS_TITLE_LEN,   a["title"]);
        copy_field(news_articles[i].summary, NEWS_SUMMARY_LEN, a["summary"]);
        copy_field(news_articles[i].source,  NEWS_SOURCE_LEN,  a["source"]);
        copy_field(news_articles[i].area,    NEWS_AREA_LEN,    a["matched_area"]);
        news_articles[i].score = a["score"] | 0.0f;
        i++;
    }

    if (i == 0) {
        Serial.println("[News] 'articles' array was empty");
        return false;
    }

    news_count = i;
    news_data_version = news_data_version + 1;   /* bumped last; see note above */
    Serial.printf("[News] Loaded %d articles\n", i);
    return true;
}

static void news_task(void *arg)
{
    (void)arg;

    /* No endpoint configured — show sample data and stop. */
    if (strlen(NEWS_URL) == 0) {
        load_sample_data();
        vTaskDelete(NULL);
        return;
    }

    /* Give wifi_task a chance to associate before the first attempt. */
    vTaskDelay(pdMS_TO_TICKS(3000));

    uint32_t last_fetch = 0;
    for (;;) {
        uint32_t now = millis();
        bool due = (last_fetch == 0) || (now - last_fetch >= NEWS_REFRESH_INTERVAL_MS);

        if (due || refresh_req) {
            refresh_req = false;
            bool ok = news_refresh_once();
            news_fetch_failed = !ok;
            /* On failure retry on the next loop rather than waiting the full
             * interval, but only after a short backoff. */
            last_fetch = ok ? now : (now - NEWS_REFRESH_INTERVAL_MS + 30000);
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

void news_client_init(void)
{
    xTaskCreatePinnedToCore(news_task, "news_task", 8192, NULL, 1, NULL, 0);
}
