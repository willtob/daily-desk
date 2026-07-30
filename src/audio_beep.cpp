/*
 * ES8311 DAC + I2S beep driver.
 *
 * Matches the approach in AudioManager (rsvpnano) which is proven on this board.
 *
 * Key corrections vs. original version:
 *   1. I2S DOUT = GPIO45 (ESP32-S3 TX → ES8311 DIN).
 *      GPIO6 is ES8311 DOUT → ESP32-S3 RX (unused for playback).
 *      The original had these reversed, so audio data never reached the codec.
 *   2. Uses legacy driver/i2s.h API (i2s_driver_install / i2s_write) instead of
 *      the new i2s_std API, which has DMA descriptor lock-up issues on this
 *      platform when the channel sits idle between beeps.
 *   3. ES8311 config uses read-modify-write for REG00/REG06/REG09/REG0A/REG31.
 *   4. TCA9554 PA enable uses read-modify-write (only touches bit 7).
 *   5. Beep buffer is pre-computed at init time (square wave with envelope).
 *
 * Hardware:
 *   MCLK  GPIO7   BCLK  GPIO15   WS   GPIO46
 *   DOUT  GPIO45  (ESP32 TX → ES8311 DIN)
 *   DIN   GPIO6   (ES8311 DOUT → ESP32 RX, unused)
 *   ES8311  I2C addr 0x18, I2C_NUM_0 (SDA=47, SCL=48)
 *   TCA9554 I2C addr 0x20, same bus  — EXIO7 = PA enable
 */
#include <Arduino.h>
#include "audio_beep.h"
#include "esp_err.h"
#include "esp_log.h"
#include "driver/i2c_master.h"
#include "driver/i2s.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "beep";

/* ── Hardware addresses / pins ────────────────────────────────── */
#define ES8311_ADDR    0x18
#define TCA9554_ADDR   0x20

#define I2S_PORT         I2S_NUM_0
#define I2S_MCLK_GPIO    GPIO_NUM_7
#define I2S_BCLK_GPIO    GPIO_NUM_15
#define I2S_WS_GPIO      GPIO_NUM_46
#define I2S_DOUT_GPIO    GPIO_NUM_45   /* ESP32-S3 TX → ES8311 DIN */

/* ── Beep parameters ──────────────────────────────────────────── */
#define BEEP_SAMPLE_RATE    16000
#define BEEP_FREQ_HZ        330
#define BEEP_DURATION_MS    250
#define BEEP_AMPLITUDE      9000
#define BEEP_ATTACK_MS      120
#define BEEP_RELEASE_MS     130
#define BEEP_FRAMES         ((BEEP_SAMPLE_RATE * BEEP_DURATION_MS) / 1000)
#define BEEP_SAMPLES        (BEEP_FRAMES * 2)   /* stereo interleaved */

/* ── State ────────────────────────────────────────────────────── */
static i2c_master_dev_handle_t  es8311_handle  = NULL;
static i2c_master_dev_handle_t  tca9554_handle = NULL;
static bool                     audio_ready    = false;
static int16_t                  beep_buf[BEEP_SAMPLES];

/* ── ES8311 I2C helpers ───────────────────────────────────────── */
static esp_err_t es8311_wr(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(es8311_handle, buf, 2, pdMS_TO_TICKS(50));
}

static esp_err_t es8311_rd(uint8_t reg, uint8_t *val)
{
    return i2c_master_transmit_receive(es8311_handle, &reg, 1, val, 1,
                                       pdMS_TO_TICKS(50));
}

/* ── TCA9554 helpers ─────────────────────────────────────────── */
static esp_err_t tca9554_rd(uint8_t reg, uint8_t *val)
{
    return i2c_master_transmit_receive(tca9554_handle, &reg, 1, val, 1,
                                       pdMS_TO_TICKS(50));
}

static esp_err_t tca9554_wr(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(tca9554_handle, buf, 2, pdMS_TO_TICKS(50));
}

/* ── PA enable (read-modify-write — only touches bit 7) ──────── */
static void pa_enable(void)
{
    if (tca9554_handle == NULL) {
        i2c_master_bus_handle_t bus0 = NULL;
        if (i2c_master_get_bus_handle(I2C_NUM_0, &bus0) != ESP_OK || bus0 == NULL) {
            ESP_LOGE(TAG, "pa_enable: i2c bus 0 not ready");
            return;
        }
        i2c_device_config_t dev_cfg = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address  = TCA9554_ADDR,
            .scl_speed_hz    = 100000,
        };
        if (i2c_master_bus_add_device(bus0, &dev_cfg, &tca9554_handle) != ESP_OK) {
            ESP_LOGE(TAG, "TCA9554 add failed");
            tca9554_handle = NULL;
            return;
        }
    }

    uint8_t out_val = 0xFF, cfg_val = 0xFF;
    tca9554_rd(0x01, &out_val);
    tca9554_rd(0x03, &cfg_val);

    out_val |=  (1U << 7);   /* output latch: pin 7 = HIGH (PA on)  */
    cfg_val &= ~(1U << 7);   /* config:       pin 7 = output        */

    esp_err_t r  = tca9554_wr(0x01, out_val);
             r |= tca9554_wr(0x03, cfg_val);
    if (r != ESP_OK)
        ESP_LOGE(TAG, "TCA9554 PA enable write failed: %d", r);
    else
        ESP_LOGI(TAG, "PA enabled via TCA9554 EXIO7");
}

/* ── ES8311 init sequence (matches AudioManager / Espressif ref) ─ */
static bool es8311_configure(void)
{
    uint8_t reg;

    /* I2C noise immunity (write twice per Espressif note) */
    if (es8311_wr(0x44, 0x08) != ESP_OK) return false;
    if (es8311_wr(0x44, 0x08) != ESP_OK) return false;

    /* Initial clock manager state */
    if (es8311_wr(0x01, 0x30) != ESP_OK) return false;
    if (es8311_wr(0x02, 0x00) != ESP_OK) return false;
    if (es8311_wr(0x03, 0x10) != ESP_OK) return false;
    if (es8311_wr(0x16, 0x24) != ESP_OK) return false;
    if (es8311_wr(0x04, 0x10) != ESP_OK) return false;
    if (es8311_wr(0x05, 0x00) != ESP_OK) return false;

    /* System power */
    if (es8311_wr(0x0B, 0x00) != ESP_OK) return false;
    if (es8311_wr(0x0C, 0x00) != ESP_OK) return false;
    if (es8311_wr(0x10, 0x1F) != ESP_OK) return false;
    if (es8311_wr(0x11, 0x7F) != ESP_OK) return false;

    /* Release reset, confirm slave mode (read-modify-write REG00) */
    if (es8311_wr(0x00, 0x80) != ESP_OK) return false;
    if (es8311_rd(0x00, &reg) != ESP_OK) return false;
    if (es8311_wr(0x00, reg & 0xBF)  != ESP_OK) return false;

    /* Use external MCLK, not inverted */
    if (es8311_wr(0x01, 0x3F) != ESP_OK) return false;
    /* SCLK: clear invert bit (read-modify-write REG06) */
    if (es8311_rd(0x06, &reg) != ESP_OK) return false;
    if (es8311_wr(0x06, reg & ~0x20U) != ESP_OK) return false;

    if (es8311_wr(0x13, 0x10) != ESP_OK) return false;
    if (es8311_wr(0x1B, 0x0A) != ESP_OK) return false;
    if (es8311_wr(0x1C, 0x6A) != ESP_OK) return false;
    /* Internal reference: ADCL + DACR */
    if (es8311_wr(0x44, 0x58) != ESP_OK) return false;

    return true;
}

static bool es8311_set_sample_format(void)
{
    uint8_t dac_iface, adc_iface, reg;

    if (es8311_rd(0x09, &dac_iface) != ESP_OK) return false;
    if (es8311_rd(0x0A, &adc_iface) != ESP_OK) return false;

    /* Standard I2S, 16-bit (keep bits[7:5], set bits[4:0] = 0x0C) */
    dac_iface = (dac_iface & 0xE0U) | 0x0C;
    adc_iface = (adc_iface & 0xE0U) | 0x0C;

    if (es8311_wr(0x09, dac_iface) != ESP_OK) return false;
    if (es8311_wr(0x0A, adc_iface) != ESP_OK) return false;

    /* Clock dividers for 16 kHz, MCLK = 4096000 Hz (256 × 16000) */
    if (es8311_rd(0x02, &reg) != ESP_OK) return false;
    if (es8311_wr(0x02, reg & 0x07U) != ESP_OK) return false;    /* pre_div=1  */
    if (es8311_wr(0x05, 0x00)        != ESP_OK) return false;    /* dividers=1 */
    if (es8311_rd(0x03, &reg) != ESP_OK) return false;
    if (es8311_wr(0x03, (reg & 0x80U) | 0x10U) != ESP_OK) return false;  /* adc_osr */
    if (es8311_rd(0x04, &reg) != ESP_OK) return false;
    if (es8311_wr(0x04, (reg & 0x80U) | 0x10U) != ESP_OK) return false;  /* dac_osr */
    if (es8311_rd(0x07, &reg) != ESP_OK) return false;
    if (es8311_wr(0x07, reg & 0xC0U)           != ESP_OK) return false;  /* lrck_h=0 */
    if (es8311_wr(0x08, 0xFF)                  != ESP_OK) return false;  /* lrck_l   */
    if (es8311_rd(0x06, &reg) != ESP_OK) return false;
    if (es8311_wr(0x06, (reg & 0xE0U) | 0x03U) != ESP_OK) return false; /* bclk_div */

    return true;
}

static bool es8311_start_dac(void)
{
    uint8_t dac_iface, adc_iface, dac_mute;

    if (es8311_wr(0x00, 0x80) != ESP_OK) return false;
    if (es8311_wr(0x01, 0x3F) != ESP_OK) return false;
    if (es8311_rd(0x09, &dac_iface) != ESP_OK) return false;
    if (es8311_rd(0x0A, &adc_iface) != ESP_OK) return false;

    dac_iface &= ~(1U << 6);   /* DAC: unmuted */
    adc_iface |=  (1U << 6);   /* ADC: muted   */

    if (es8311_wr(0x09, dac_iface) != ESP_OK) return false;
    if (es8311_wr(0x0A, adc_iface) != ESP_OK) return false;
    if (es8311_wr(0x17, 0xBF)      != ESP_OK) return false;
    if (es8311_wr(0x0E, 0x02)      != ESP_OK) return false;
    if (es8311_wr(0x12, 0x00)      != ESP_OK) return false;
    if (es8311_wr(0x14, 0x1A)      != ESP_OK) return false;
    if (es8311_wr(0x0D, 0x01)      != ESP_OK) return false;
    if (es8311_wr(0x15, 0x40)      != ESP_OK) return false;
    if (es8311_wr(0x37, 0x08)      != ESP_OK) return false;
    if (es8311_wr(0x45, 0x00)      != ESP_OK) return false;

    /* Clear DAC mute bits [5:4] in REG31 */
    if (es8311_rd(0x31, &dac_mute) != ESP_OK) return false;
    if (es8311_wr(0x31, dac_mute & 0x9F) != ESP_OK) return false;

    /* Volume: maximum gain */
    if (es8311_wr(0x32, 0xFF) != ESP_OK) return false;

    return true;
}

/* ── Pre-compute square-wave beep buffer with attack/release ──── */
static void fill_beep_buffer(void)
{
    const uint32_t half_period   = BEEP_SAMPLE_RATE / (BEEP_FREQ_HZ * 2U);
    const uint32_t attack_frames = (BEEP_SAMPLE_RATE * BEEP_ATTACK_MS)  / 1000U;
    const uint32_t release_frames= (BEEP_SAMPLE_RATE * BEEP_RELEASE_MS) / 1000U;

    for (int f = 0; f < BEEP_FRAMES; f++) {
        int32_t s = ((f / (int)half_period) % 2 == 0) ? BEEP_AMPLITUDE : -BEEP_AMPLITUDE;

        if ((uint32_t)f < attack_frames) {
            s = (s * f) / (int32_t)attack_frames;
        } else if (f >= BEEP_FRAMES - (int)release_frames) {
            s = (s * (BEEP_FRAMES - f)) / (int32_t)release_frames;
        }

        beep_buf[f * 2]     = (int16_t)s;
        beep_buf[f * 2 + 1] = (int16_t)s;
    }
}

/* ── Initialisation ───────────────────────────────────────────── */
void audio_beep_init(void)
{
    Serial.println("[beep] audio_beep_init called");

    fill_beep_buffer();

    /* ── 1. PA enable ── */
    pa_enable();
    vTaskDelay(pdMS_TO_TICKS(15));   /* audio rail stabilise */

    /* ── 2. I2S (legacy driver/i2s.h — proven DMA behaviour) ── */
    i2s_config_t i2s_cfg = {};
    i2s_cfg.mode                = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
    i2s_cfg.sample_rate         = BEEP_SAMPLE_RATE;
    i2s_cfg.bits_per_sample     = I2S_BITS_PER_SAMPLE_16BIT;
    i2s_cfg.channel_format      = I2S_CHANNEL_FMT_RIGHT_LEFT;
    i2s_cfg.communication_format= I2S_COMM_FORMAT_STAND_I2S;
    i2s_cfg.intr_alloc_flags    = 0;
    i2s_cfg.dma_buf_count       = 4;
    i2s_cfg.dma_buf_len         = 240;
    i2s_cfg.use_apll            = false;
    i2s_cfg.tx_desc_auto_clear  = true;
    i2s_cfg.fixed_mclk          = 0;
    i2s_cfg.mclk_multiple       = I2S_MCLK_MULTIPLE_256;

    esp_err_t ret = i2s_driver_install(I2S_PORT, &i2s_cfg, 0, nullptr);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_driver_install failed: %d", ret);
        return;
    }

    i2s_pin_config_t pin_cfg = {};
    pin_cfg.mck_io_num   = I2S_MCLK_GPIO;
    pin_cfg.bck_io_num   = I2S_BCLK_GPIO;
    pin_cfg.ws_io_num    = I2S_WS_GPIO;
    pin_cfg.data_out_num = I2S_DOUT_GPIO;      /* GPIO45 → ES8311 DIN */
    pin_cfg.data_in_num  = I2S_PIN_NO_CHANGE;

    ret = i2s_set_pin(I2S_PORT, &pin_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_set_pin failed: %d", ret);
        return;
    }

    ret = i2s_set_clk(I2S_PORT, BEEP_SAMPLE_RATE,
                      I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_set_clk failed: %d", ret);
        return;
    }

    i2s_zero_dma_buffer(I2S_PORT);

    /* ── 3. ES8311: register on I2C bus and initialise ── */
    i2c_master_bus_handle_t bus0 = NULL;
    ret = i2c_master_get_bus_handle(I2C_NUM_0, &bus0);
    if (ret != ESP_OK || bus0 == NULL) {
        ESP_LOGE(TAG, "i2c bus 0 not ready");
        return;
    }

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = ES8311_ADDR,
        .scl_speed_hz    = 300000,
    };
    ret = i2c_master_bus_add_device(bus0, &dev_cfg, &es8311_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ES8311 add failed: %d", ret);
        return;
    }

    if (!es8311_configure() || !es8311_set_sample_format() || !es8311_start_dac()) {
        ESP_LOGE(TAG, "ES8311 init failed");
        return;
    }

    ESP_LOGI(TAG, "ES8311 initialised");
    audio_ready = true;
    ESP_LOGI(TAG, "Audio ready");
}

/* ── Play beep ────────────────────────────────────────────────── */
void audio_beep_play(void)
{
    Serial.println("[beep] audio_beep_play called");
    if (!audio_ready) return;

    pa_enable();

    /* Ensure DAC is unmuted and at max volume before every beep */
    uint8_t dac_mute = 0;
    if (es8311_rd(0x31, &dac_mute) == ESP_OK)
        es8311_wr(0x31, dac_mute & 0x9F);
    es8311_wr(0x32, 0xFF);

    /* Write buffer; retry once after I2S restart if write fails */
    const uint8_t *data  = (const uint8_t *)beep_buf;
    const size_t   total = sizeof(beep_buf);
    size_t written = 0;
    bool   ok      = true;

    while (written < total) {
        size_t n = 0;
        esp_err_t err = i2s_write(I2S_PORT, data + written,
                                  total - written, &n, pdMS_TO_TICKS(250));
        if (err != ESP_OK || n == 0) { ok = false; break; }
        written += n;
    }

    if (!ok) {
        Serial.println("[beep] I2S write failed, restarting");
        i2s_stop(I2S_PORT);
        i2s_start(I2S_PORT);
        i2s_zero_dma_buffer(I2S_PORT);
        written = 0;
        while (written < total) {
            size_t n = 0;
            i2s_write(I2S_PORT, data + written, total - written, &n, pdMS_TO_TICKS(250));
            if (n == 0) break;
            written += n;
        }
    }

    Serial.printf("[beep] I2S wrote %d / %d bytes\n", (int)written, (int)total);
}
