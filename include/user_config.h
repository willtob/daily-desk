#ifndef USER_CONFIG_H
#define USER_CONFIG_H

#define LCD_HOST SPI3_HOST

/* Touch I2C (port 1) */
#define Touch_SCL_NUM (GPIO_NUM_18)
#define Touch_SDA_NUM (GPIO_NUM_17)

/* Main I2C bus (port 0) — TCA9554, RTC, IMU, ES8311 */
#define ESP_SCL_NUM (GPIO_NUM_48)
#define ESP_SDA_NUM (GPIO_NUM_47)

/* LCD QSPI */
#define EXAMPLE_PIN_NUM_LCD_CS    (GPIO_NUM_9)
#define EXAMPLE_PIN_NUM_LCD_PCLK  (GPIO_NUM_10)
#define EXAMPLE_PIN_NUM_LCD_DATA0 (GPIO_NUM_11)
#define EXAMPLE_PIN_NUM_LCD_DATA1 (GPIO_NUM_12)
#define EXAMPLE_PIN_NUM_LCD_DATA2 (GPIO_NUM_13)
#define EXAMPLE_PIN_NUM_LCD_DATA3 (GPIO_NUM_14)
#define EXAMPLE_PIN_NUM_LCD_RST   (GPIO_NUM_21)
#define EXAMPLE_PIN_NUM_BK_LIGHT  (GPIO_NUM_8)

/* I2C device addresses */
#define I2C_TOUCH_ADDR   0x3b
#define EXAMPLE_RTC_ADDR 0x51
#define EXAMPLE_IMU_ADDR 0x6b

/* Touch not using RST/INT pins */
#define EXAMPLE_PIN_NUM_TOUCH_RST (-1)
#define EXAMPLE_PIN_NUM_TOUCH_INT (-1)

/* LVGL tick / task tuning */
#define EXAMPLE_LVGL_TICK_PERIOD_MS    5
#define EXAMPLE_LVGL_TASK_MAX_DELAY_MS 500
#define EXAMPLE_LVGL_TASK_MIN_DELAY_MS 5

/* Display rotation */
#define USER_DISP_ROT_NONO 0
#define USER_DISP_ROT_90   1
#define USER_DISP_ROT_180  2
#define Rotated USER_DISP_ROT_180

/* Physical resolution */
#define LCD_NOROT_HRES 172
#define LCD_NOROT_VRES 640

#if (Rotated == USER_DISP_ROT_90)
#define EXAMPLE_LCD_H_RES 640
#define EXAMPLE_LCD_V_RES 172
#else
/* NONO and ROT_180 both use portrait dimensions */
#define EXAMPLE_LCD_H_RES 172
#define EXAMPLE_LCD_V_RES 640
#endif

/* LVGL buffer sizes */
#define LVGL_DMA_BUFF_LEN    (LCD_NOROT_HRES * 64 * 2)
#define LVGL_SPIRAM_BUFF_LEN (EXAMPLE_LCD_H_RES * EXAMPLE_LCD_V_RES * 2)

/* BOOT button */
#define BOOT_BUTTON_GPIO GPIO_NUM_0

#endif /* USER_CONFIG_H */
