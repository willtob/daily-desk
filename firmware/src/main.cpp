#include <Arduino.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "user_config.h"
#include "lvgl_port.h"
#include "i2c_bsp.h"
#include "wifi_manager.h"
#include "news_client.h"
#include "news_audio.h"
#include "drv/lcd_bl_bsp/lcd_bl_pwm_bsp.h"
#include "esp_err.h"

/* Init order is load-bearing — see the pomodoro CLAUDE.md notes.
 * No audio here: the news reader doesn't beep, so ES8311/I2S is left out. */
void setup()
{
    Serial.begin(115200);

    /* 1. I2C buses (port 0: TCA9554/RTC/IMU; port 1: touch) */
    i2c_master_Init();

    /* 2. Display + LVGL + touch; calls news_ui_create() inside the lock */
    lvgl_port_init();

    /* 3. Backlight on only after the display is ready */
    lcd_bl_pwm_bsp_init(LCD_PWM_MODE_255);

    /* 4. Wi-Fi association (own task) */
    wifi_manager_init();

    /* 5. Digest polling (own task) — falls back to sample data if NEWS_URL
     *    is empty, so the UI is usable before the backend exists. */
    news_client_init();

    /* 6. ES8311 + I2S + narration task. After i2c_master_Init(), which it
     *    borrows the bus 0 handle from. */
    news_audio_init();
}

void loop()
{
    /* Nothing — all work is in FreeRTOS tasks */
    vTaskDelay(portMAX_DELAY);
}
