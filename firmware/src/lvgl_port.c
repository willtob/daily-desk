/*
 * lvgl_port.c — display + touch initialisation for ESP32-S3-Touch-LCD-3.49
 *
 * Copied verbatim from Examples/Arduino/09_LVGL_V8_Test/lvgl_port.c
 * ONE change: lv_demo_widgets() replaced with news_ui_create()
 *
 * Runtime rotation is NOT wired up in this project: the news UI is laid out
 * for portrait 172x640 with compile-time constants, and nothing here calls
 * lvgl_port_set_rotation() (the IMU orientation module was not copied over).
 * To add it, give news_ui.cpp a rebuild entry point that takes runtime
 * width/height and call it from lvgl_port_set_rotation() below.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl_port.h"
#include "lvgl.h"
#include "esp_log.h"
#include "esp_err.h"
#include "esp_timer.h"
#include "user_config.h"
#include "driver/spi_master.h"
#include "esp_lcd_io_spi.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_io.h"
#include "drv/axs15231b/esp_lcd_axs15231b.h"
#include "news_ui.h"
#include "i2c_bsp.h"

#define LCD_BIT_PER_PIXEL (16)

static const char *TAG = "lvgl_port";
static SemaphoreHandle_t lvgl_mux = NULL;

static uint16_t *lvgl_dma_buf = NULL;
static SemaphoreHandle_t lvgl_flush_semap;

/* Scratch buffer holding the panel-oriented (physical 172x640) frame after
 * software rotation. Always allocated now that rotation is runtime. */
static uint16_t *rotat_ptr = NULL;

/* Current rotation, changed at runtime by lvgl_port_set_rotation(). */
static volatile int s_rotation = DISP_ROT_180;

/* Kept at file scope so lvgl_port_set_rotation() can update the resolution. */
static lv_disp_drv_t *s_disp_drv_p = NULL;
static lv_disp_t     *s_disp        = NULL;

static const axs15231b_lcd_init_cmd_t lcd_init_cmds[] =
{
  {0x11, (uint8_t []){0x00}, 0, 100},
  {0x29, (uint8_t []){0x00}, 0, 100},
};

static bool example_notify_lvgl_flush_ready(esp_lcd_panel_io_handle_t panel_io,
                                            esp_lcd_panel_io_event_data_t *edata,
                                            void *user_ctx)
{
  BaseType_t TaskWoken;
  xSemaphoreGiveFromISR(lvgl_flush_semap, &TaskWoken);
  return false;
}

static void example_increase_lvgl_tick(void *arg)
{
  lv_tick_inc(EXAMPLE_LVGL_TICK_PERIOD_MS);
}

static void example_lvgl_flush_cb(lv_disp_drv_t *drv, const lv_area_t *area, lv_color_t *color_map)
{
  /* Rotate LVGL's logical framebuffer into the physical panel layout
   * (always 172 wide x 640 tall, row-major). The band-draw loop below then
   * pushes that physical buffer to the panel unchanged. */
  uint16_t *src = (uint16_t *)color_map;
  uint16_t *map;
  const int rot = s_rotation;

  if (rot == DISP_ROT_0) {
    map = src;                         /* logical == physical */
  } else {
    map = rotat_ptr;
    if (rot == DISP_ROT_180) {
      /* 180° = reverse the entire pixel buffer */
      uint32_t total = LCD_NOROT_HRES * LCD_NOROT_VRES;
      for (uint32_t i = 0; i < total; i++)
        rotat_ptr[i] = src[total - 1 - i];
    } else if (rot == DISP_ROT_90) {
      /* logical 640x172 -> physical 172x640 */
      uint32_t idx = 0;
      for (int j = 0; j < LCD_NOROT_VRES; j++)          /* 0..639 */
        for (int i = 0; i < LCD_NOROT_HRES; i++)        /* 0..171 */
          rotat_ptr[idx++] = src[(LCD_NOROT_HRES - 1 - i) * LCD_NOROT_VRES + j];
    } else { /* DISP_ROT_270 */
      uint32_t idx = 0;
      for (int j = 0; j < LCD_NOROT_VRES; j++)
        for (int i = 0; i < LCD_NOROT_HRES; i++)
          rotat_ptr[idx++] = src[i * LCD_NOROT_VRES + (LCD_NOROT_VRES - 1 - j)];
    }
  }

  esp_lcd_panel_handle_t panel_handle = (esp_lcd_panel_handle_t) drv->user_data;
  const int flush_coun = (LVGL_SPIRAM_BUFF_LEN / LVGL_DMA_BUFF_LEN);
  const int offgap    = (LCD_NOROT_VRES / flush_coun);
  const int dmalen    = (LVGL_DMA_BUFF_LEN / 2);
  int offsetx1 = 0, offsety1 = 0;
  int offsetx2 = LCD_NOROT_HRES;
  int offsety2 = offgap;

  xSemaphoreGive(lvgl_flush_semap);
  for (int i = 0; i < flush_coun; i++) {
    xSemaphoreTake(lvgl_flush_semap, portMAX_DELAY);
    memcpy(lvgl_dma_buf, map, LVGL_DMA_BUFF_LEN);
    esp_lcd_panel_draw_bitmap(panel_handle, offsetx1, offsety1, offsetx2, offsety2, lvgl_dma_buf);
    offsety1 += offgap;
    offsety2 += offgap;
    map += dmalen;
  }
  xSemaphoreTake(lvgl_flush_semap, portMAX_DELAY);
  lv_disp_flush_ready(drv);
}

static void example_lvgl_touch_cb(lv_indev_drv_t *drv, lv_indev_data_t *data)
{
  uint8_t read_touchpad_cmd[11] = {0xb5, 0xab, 0xa5, 0x5a, 0x0, 0x0, 0x0, 0x0e, 0x0, 0x0, 0x0};
  uint8_t buff[32] = {0};
  memset(buff, 0, 32);
  ESP_ERROR_CHECK_WITHOUT_ABORT(
    i2c_master_write_read_dev(disp_touch_dev_handle, read_touchpad_cmd, 11, buff, 32));

  uint16_t pointX = (((uint16_t)buff[2] & 0x0f) << 8) | (uint16_t)buff[3];
  uint16_t pointY = (((uint16_t)buff[4] & 0x0f) << 8) | (uint16_t)buff[5];

  if (buff[1] > 0 && buff[1] < 5) {
    data->state = LV_INDEV_STATE_PR;
    /* Raw coords: pointX along the long axis (0..640), pointY along the
     * short axis (0..172). Map to LVGL logical coords per current rotation. */
    if (pointX > LCD_NOROT_VRES) pointX = LCD_NOROT_VRES;
    if (pointY > LCD_NOROT_HRES) pointY = LCD_NOROT_HRES;
    switch (s_rotation) {
      case DISP_ROT_0:            /* portrait 172x640 */
        data->point.x = pointY;
        data->point.y = (LCD_NOROT_VRES - pointX);
        break;
      case DISP_ROT_180:          /* portrait, flipped */
        data->point.x = (LCD_NOROT_HRES - pointY);
        data->point.y = pointX;
        break;
      case DISP_ROT_90:           /* landscape 640x172 */
        data->point.x = (LCD_NOROT_VRES - pointX);
        data->point.y = (LCD_NOROT_HRES - pointY);
        break;
      case DISP_ROT_270:          /* landscape, flipped */
        data->point.x = pointX;
        data->point.y = pointY;
        break;
    }
  } else {
    data->state = LV_INDEV_STATE_REL;
  }
}

static bool example_lvgl_lock(int timeout_ms)
{
  const TickType_t ticks = (timeout_ms == -1) ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);
  return xSemaphoreTake(lvgl_mux, ticks) == pdTRUE;
}

static void example_lvgl_unlock(void)
{
  assert(lvgl_mux && "bsp_display_start must be called first");
  xSemaphoreGive(lvgl_mux);
}

void example_lvgl_port_task(void *arg)
{
  uint32_t task_delay_ms = EXAMPLE_LVGL_TASK_MAX_DELAY_MS;
  for (;;) {
    if (example_lvgl_lock(-1)) {
      task_delay_ms = lv_timer_handler();
      example_lvgl_unlock();
    }
    if (task_delay_ms > EXAMPLE_LVGL_TASK_MAX_DELAY_MS)
      task_delay_ms = EXAMPLE_LVGL_TASK_MAX_DELAY_MS;
    else if (task_delay_ms < EXAMPLE_LVGL_TASK_MIN_DELAY_MS)
      task_delay_ms = EXAMPLE_LVGL_TASK_MIN_DELAY_MS;
    vTaskDelay(pdMS_TO_TICKS(task_delay_ms));
  }
}

void lvgl_port_init(void)
{
  /* Physical panel is always 172x640; the rotation scratch buffer matches. */
  rotat_ptr = (uint16_t*)heap_caps_malloc(
    LCD_NOROT_HRES * LCD_NOROT_VRES * sizeof(uint16_t), MALLOC_CAP_SPIRAM);
  assert(rotat_ptr);
  lvgl_flush_semap = xSemaphoreCreateBinary();

  static lv_disp_draw_buf_t disp_buf;
  static lv_disp_drv_t      disp_drv;

  ESP_LOGI(TAG, "Initialize LCD RST GPIO");
  gpio_config_t gpio_conf = {};
  gpio_conf.intr_type    = GPIO_INTR_DISABLE;
  gpio_conf.mode         = GPIO_MODE_OUTPUT;
  gpio_conf.pin_bit_mask = ((uint64_t)0x01 << EXAMPLE_PIN_NUM_LCD_RST);
  gpio_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
  gpio_conf.pull_up_en   = GPIO_PULLUP_ENABLE;
  ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_config(&gpio_conf));

  ESP_LOGI(TAG, "Initialize QSPI bus");
  spi_bus_config_t buscfg = {};
  buscfg.data0_io_num    = EXAMPLE_PIN_NUM_LCD_DATA0;
  buscfg.data1_io_num    = EXAMPLE_PIN_NUM_LCD_DATA1;
  buscfg.sclk_io_num     = EXAMPLE_PIN_NUM_LCD_PCLK;
  buscfg.data2_io_num    = EXAMPLE_PIN_NUM_LCD_DATA2;
  buscfg.data3_io_num    = EXAMPLE_PIN_NUM_LCD_DATA3;
  buscfg.max_transfer_sz = LVGL_DMA_BUFF_LEN;
  ESP_ERROR_CHECK(spi_bus_initialize(LCD_HOST, &buscfg, SPI_DMA_CH_AUTO));

  ESP_LOGI(TAG, "Install panel IO");
  esp_lcd_panel_io_handle_t panel_io = NULL;
  esp_lcd_panel_handle_t    panel    = NULL;

  esp_lcd_panel_io_spi_config_t io_config = {};
  io_config.cs_gpio_num           = EXAMPLE_PIN_NUM_LCD_CS;
  io_config.dc_gpio_num           = -1;
  io_config.spi_mode              = 3;
  io_config.pclk_hz               = 40 * 1000 * 1000;
  io_config.trans_queue_depth     = 10;
  io_config.on_color_trans_done   = example_notify_lvgl_flush_ready;
  io_config.lcd_cmd_bits          = 32;
  io_config.lcd_param_bits        = 8;
  io_config.flags.quad_mode       = true;
  ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(LCD_HOST, &io_config, &panel_io));

  axs15231b_vendor_config_t vendor_config = {};
  vendor_config.flags.use_qspi_interface = 1;
  vendor_config.init_cmds      = lcd_init_cmds;
  vendor_config.init_cmds_size = sizeof(lcd_init_cmds) / sizeof(lcd_init_cmds[0]);

  esp_lcd_panel_dev_config_t panel_config = {};
  panel_config.reset_gpio_num  = -1;
  panel_config.rgb_ele_order   = LCD_RGB_ELEMENT_ORDER_RGB;
  panel_config.bits_per_pixel  = LCD_BIT_PER_PIXEL;
  panel_config.vendor_config   = &vendor_config;

  ESP_LOGI(TAG, "Install panel driver");
  ESP_ERROR_CHECK(esp_lcd_new_panel_axs15231b(panel_io, &panel_config, &panel));

  ESP_ERROR_CHECK(gpio_set_level(EXAMPLE_PIN_NUM_LCD_RST, 1));
  vTaskDelay(pdMS_TO_TICKS(30));
  ESP_ERROR_CHECK(gpio_set_level(EXAMPLE_PIN_NUM_LCD_RST, 0));
  vTaskDelay(pdMS_TO_TICKS(250));
  ESP_ERROR_CHECK(gpio_set_level(EXAMPLE_PIN_NUM_LCD_RST, 1));
  vTaskDelay(pdMS_TO_TICKS(30));
  ESP_ERROR_CHECK(esp_lcd_panel_init(panel));

  lv_init();

  lvgl_dma_buf = (uint16_t *)heap_caps_malloc(LVGL_DMA_BUFF_LEN, MALLOC_CAP_DMA);
  assert(lvgl_dma_buf);
  lv_color_t *buffer_1 = (lv_color_t *)heap_caps_malloc(LVGL_SPIRAM_BUFF_LEN, MALLOC_CAP_SPIRAM);
  lv_color_t *buffer_2 = (lv_color_t *)heap_caps_malloc(LVGL_SPIRAM_BUFF_LEN, MALLOC_CAP_SPIRAM);
  assert(buffer_1);
  assert(buffer_2);
  lv_disp_draw_buf_init(&disp_buf, buffer_1, buffer_2, EXAMPLE_LCD_H_RES * EXAMPLE_LCD_V_RES);

  ESP_LOGI(TAG, "Register display driver to LVGL");
  lv_disp_drv_init(&disp_drv);
  disp_drv.hor_res      = EXAMPLE_LCD_H_RES;
  disp_drv.ver_res      = EXAMPLE_LCD_V_RES;
  disp_drv.flush_cb     = example_lvgl_flush_cb;
  disp_drv.draw_buf     = &disp_buf;
  disp_drv.full_refresh = 1;   /* MUST stay 1 for AXS15231B */
  disp_drv.user_data    = panel;
  s_disp_drv_p = &disp_drv;
  s_disp = lv_disp_drv_register(&disp_drv);

  ESP_LOGI(TAG, "Install LVGL tick timer");
  esp_timer_create_args_t lvgl_tick_timer_args = {};
  lvgl_tick_timer_args.callback = &example_increase_lvgl_tick;
  lvgl_tick_timer_args.name     = "lvgl_tick";
  esp_timer_handle_t lvgl_tick_timer = NULL;
  ESP_ERROR_CHECK(esp_timer_create(&lvgl_tick_timer_args, &lvgl_tick_timer));
  ESP_ERROR_CHECK(esp_timer_start_periodic(lvgl_tick_timer,
                                           EXAMPLE_LVGL_TICK_PERIOD_MS * 1000));

  static lv_indev_drv_t indev_drv;
  lv_indev_drv_init(&indev_drv);
  indev_drv.type    = LV_INDEV_TYPE_POINTER;
  indev_drv.read_cb = example_lvgl_touch_cb;
  lv_indev_drv_register(&indev_drv);

  lvgl_mux = xSemaphoreCreateMutex();
  assert(lvgl_mux);
  xTaskCreatePinnedToCore(example_lvgl_port_task, "LVGL", 4096, NULL, 4, NULL, 0);

  /* Create the news UI inside the lock — safe widget creation */
  if (example_lvgl_lock(-1)) {
    news_ui_create();
    example_lvgl_unlock();
  }
}

int lvgl_port_get_rotation(void)
{
  return s_rotation;
}

void lvgl_port_set_rotation(int deg)
{
  if (deg != DISP_ROT_0 && deg != DISP_ROT_90 &&
      deg != DISP_ROT_180 && deg != DISP_ROT_270)
    return;
  if (deg == s_rotation) return;
  if (!s_disp_drv_p) return;

  if (!example_lvgl_lock(-1)) return;

  s_rotation = deg;
  bool landscape = (deg == DISP_ROT_90 || deg == DISP_ROT_270);
  s_disp_drv_p->hor_res = landscape ? LCD_NOROT_VRES : LCD_NOROT_HRES;  /* 640 : 172 */
  s_disp_drv_p->ver_res = landscape ? LCD_NOROT_HRES : LCD_NOROT_VRES;  /* 172 : 640 */
  lv_disp_drv_update(s_disp, s_disp_drv_p);

  /* No UI rebuild hook here — the news UI is portrait-only (see file header).
   * Nothing in this project calls this function; it is kept so the driver
   * stays a verbatim copy of the Waveshare original. */

  example_lvgl_unlock();
}
