/*
 * news_audio.cpp — streams narration from the backend to the speaker.
 *
 * The backend returns raw PCM, so playback is: read a chunk off the socket,
 * widen mono to stereo, i2s_write, repeat. No decoder, no full-file buffer —
 * a 20-second article is ~1 MB, which would fit in PSRAM but would also mean
 * a second of silence before anything started.
 *
 * Everything happens on its own task. The UI only ever sets a request flag
 * and reads the volatile state, so nothing blocks the LVGL task.
 */
#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <driver/i2s.h>          /* legacy API on purpose — see CLAUDE.md */
#include "news_audio.h"
#include "news_client.h"
#include "audio_beep.h"
#include "wifi_manager.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define I2S_PORT        I2S_NUM_0
#define HTTP_TIMEOUT_MS 15000

/* Mono samples per read. 512 frames is ~21 ms of audio at 24 kHz — small
 * enough that a stop request is acted on promptly, large enough that the
 * socket isn't read a handful of bytes at a time. */
#define CHUNK_FRAMES    512

volatile bool news_audio_playing = false;
volatile int  news_audio_index   = -1;
volatile bool news_audio_failed  = false;

static volatile int  play_req = -1;      /* article index requested, -1 = none */
static volatile bool stop_req = false;

static int16_t mono_buf[CHUNK_FRAMES];
static int16_t stereo_buf[CHUNK_FRAMES * 2];

void news_audio_play(int article_index)
{
    stop_req = false;
    play_req = article_index;
}

void news_audio_stop(void)
{
    stop_req = true;
}

/* Play one article. Returns true if it streamed to completion. */
static bool stream_article(int index)
{
    if (wifi_conn_status != WIFI_CONNECTED) {
        Serial.println("[Audio] No Wi-Fi");
        return false;
    }
    if (strlen(NEWS_BASE_URL) == 0) {
        Serial.println("[Audio] NEWS_BASE_URL not configured");
        return false;
    }

    char url[160];
    snprintf(url, sizeof(url), "%s/audio/%d.pcm", NEWS_BASE_URL, index);
    Serial.printf("[Audio] GET %s\n", url);

    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    if (!http.begin(url)) {
        Serial.println("[Audio] http.begin() failed");
        return false;
    }

    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        /* 503 means the backend has no API key; 404 means a stale index. */
        Serial.printf("[Audio] HTTP %d\n", code);
        http.end();
        return false;
    }

    int total = http.getSize();
    Serial.printf("[Audio] streaming %d bytes (~%.1fs)\n",
                  total, total / (float)(NEWS_AUDIO_SAMPLE_RATE * 2));

    /* The codec is initialised for the beep's 16 kHz; speech is 24 kHz. */
    i2s_set_clk(I2S_PORT, NEWS_AUDIO_SAMPLE_RATE,
                I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO);
    i2s_zero_dma_buffer(I2S_PORT);

    WiFiClient *stream = http.getStreamPtr();
    size_t written_total = 0;
    bool   ok = true;

    while (http.connected() && !stop_req) {
        size_t avail = stream->available();
        if (avail == 0) {
            /* getSize() is -1 for chunked responses; end on socket close. */
            if (total >= 0 && written_total >= (size_t)total) break;
            if (!stream->connected()) break;
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }

        size_t want = avail > sizeof(mono_buf) ? sizeof(mono_buf) : avail;
        want &= ~((size_t)1);                 /* keep 16-bit alignment */
        if (want == 0) { vTaskDelay(pdMS_TO_TICKS(5)); continue; }

        int got = stream->readBytes((uint8_t *)mono_buf, want);
        if (got <= 0) break;
        written_total += got;

        /* Widen mono to stereo — the codec is running a 2-channel frame. */
        int frames = got / 2;
        for (int i = 0; i < frames; i++) {
            stereo_buf[i * 2]     = mono_buf[i];
            stereo_buf[i * 2 + 1] = mono_buf[i];
        }

        size_t written = 0;
        esp_err_t err = i2s_write(I2S_PORT, stereo_buf, frames * 2 * sizeof(int16_t),
                                  &written, portMAX_DELAY);
        if (err != ESP_OK) {
            Serial.printf("[Audio] i2s_write failed: %d\n", err);
            ok = false;
            break;
        }
    }

    /* Let the DMA drain before muting, otherwise the tail is clipped. */
    vTaskDelay(pdMS_TO_TICKS(60));
    i2s_zero_dma_buffer(I2S_PORT);
    http.end();

    if (stop_req) {
        Serial.printf("[Audio] stopped after %u bytes\n", (unsigned)written_total);
        return true;
    }
    Serial.printf("[Audio] done, %u bytes\n", (unsigned)written_total);
    return ok && written_total > 0;
}

static void audio_task(void *arg)
{
    (void)arg;
    for (;;) {
        int req = play_req;
        if (req >= 0) {
            play_req = -1;
            news_audio_index   = req;
            news_audio_playing = true;
            news_audio_failed  = false;

            bool ok = stream_article(req);

            news_audio_playing = false;
            news_audio_index   = -1;
            news_audio_failed  = !ok;
            stop_req = false;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

void news_audio_init(void)
{
    /* Brings up ES8311 over I2C, installs the legacy I2S driver and asserts
     * PA_EN on the TCA9554 — identical to what the beep needs. */
    audio_beep_init();

    /* 6 KB stack: HTTPClient plus TLS-free socket handling. */
    xTaskCreatePinnedToCore(audio_task, "audio_task", 6144, NULL, 2, NULL, 1);
}
