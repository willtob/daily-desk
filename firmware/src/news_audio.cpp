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

/* ── Volume ───────────────────────────────────────────────────────────────
 *
 * THIS is the knob to turn. audio_beep_init() leaves the ES8311 DAC volume at
 * 0xFF (0 dB, maximum) and the beep copes by generating its square wave at an
 * amplitude of only 9000 of 32767. Speech arrives near full scale, so at 0xFF
 * the amplifier and the small MX1.25 speaker are driven flat out — which reads
 * as *both* too loud and muffled, because overdriving them adds distortion on
 * top of the level.
 *
 * Attenuating in software instead does not help: it shrinks the digital signal
 * but the analog stage still runs at full gain. Lower the DAC volume here.
 *
 * 0xFF = 0 dB, -0.5 dB per step down:
 *   0xE0 ~ -15.5 dB     0xD0 ~ -23.5 dB     0xC0 ~ -31.5 dB
 * Too loud/muffled -> lower it. Too quiet -> raise it, a few steps at a time. */
#define NEWS_AUDIO_DAC_VOLUME    0xC8      /* ~ -27.5 dB */

/* Digital gain is left at unity so there is exactly one volume control.
 * Only reach for this if the DAC volume alone can't get you there. */
#define NEWS_AUDIO_GAIN_PERCENT  100

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
    Serial.printf("[Audio] streaming %d bytes (~%.1fs) gain=%d%% dacvol=0x%02X\n",
                  total, total / (float)(NEWS_AUDIO_SAMPLE_RATE * 2),
                  NEWS_AUDIO_GAIN_PERCENT, NEWS_AUDIO_DAC_VOLUME);

    /* Same rate the codec was configured for at init — the backend resamples
     * to match, so this only restates what audio_beep_init() already set. */
    i2s_set_clk(I2S_PORT, NEWS_AUDIO_SAMPLE_RATE,
                I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO);
    i2s_zero_dma_buffer(I2S_PORT);

    /* Back off the analog gain before any audio reaches the amplifier. */
    audio_set_dac_volume(NEWS_AUDIO_DAC_VOLUME);

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

        /* Widen mono to stereo (the codec runs a 2-channel frame) and apply
         * playback gain. int32 intermediate so the multiply can't wrap. */
        int frames = got / 2;
        for (int i = 0; i < frames; i++) {
            int32_t s = ((int32_t)mono_buf[i] * NEWS_AUDIO_GAIN_PERCENT) / 100;
            if (s >  32767) s =  32767;      /* only reachable if gain > 100 */
            if (s < -32768) s = -32768;
            stereo_buf[i * 2]     = (int16_t)s;
            stereo_buf[i * 2 + 1] = (int16_t)s;
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
