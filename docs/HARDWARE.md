# Waveshare ESP32-S3-Touch-LCD-3.49 Hardware Notes

> **Copy.** The original lives at `~/Desktop/ESP32/esp32-projects/HARDWARE.md`
> and is shared with the PomodoroTimer / RSVPNano projects. This copy is here
> so the firmware in `../firmware/` is self-contained. If you change board-level
> facts, change the original too — otherwise these drift.
>
> Note: paths inside referring to `~/Documents/...` are stale; everything is
> under `~/Desktop/ESP32/`. The News Display firmware has since moved into this
> repo at `../firmware/`.

## Board Identity
- **Model:** Waveshare ESP32-S3-Touch-LCD-3.49
- **Chip:** ESP32-S3R8 (240MHz, 8MB OPI PSRAM, 16MB QIO Flash)
- **Waveshare Wiki:** waveshare.com/wiki/ESP32-S3-Touch-LCD-3.49
- **Waveshare GitHub:** github.com/waveshareteam/ESP32-S3-Touch-LCD-3.49

---

## Pin Assignments

| Peripheral | GPIO / Address | Notes |
|---|---|---|
| Display | SPI3, CS=9, CLK=10, D0=11, D1=12, D2=13, D3=14, RST=21 | AXS15231B, QSPI, 172x640 |
| Backlight | GPIO 8 | LEDC channel 1 / timer 3, 8-bit PWM |
| Touch | I2C port 1, SDA=17, SCL=18, addr 0x3B | Capacitive, AXS15231B |
| Main I2C bus | I2C port 0, SDA=47, SCL=48 | Shared by RTC, IMU, audio, I/O expander |
| Audio DAC | I2C addr 0x18 (ES8311) | I2S: MCLK=7, BCLK=15, WS=46, DOUT=6 |
| Audio ADC | ES7210 | Dual microphone array |
| PA enable | I2C addr 0x20 (TCA9554), EXIO7 HIGH | Must set before playing audio |
| RTC | I2C addr 0x51 (PCF85063) | Time retention |
| IMU | I2C addr 0x6B (QMI8658) | 3-axis gyro + 3-axis accelerometer |
| TCA9554 expander | I2C addr 0x20, INT=IO42 | Controls backlight, PA, IMU ints, RTC int |
| SD Card | CS=38, MOSI=39, MISO=40, SCLK=41 | FAT32 format required |
| BOOT button | GPIO 0, active-low, pull-up | Usable as user button |
| Speaker | MX1.25 2PIN header (back side) | External speaker required |
| USB | Type-C (back side) | Programming and serial monitor |

## TCA9554 GPIO Expander Pin Map
| EXIO | Function | Direction |
|------|----------|-----------|
| EXIO0 | TP_INT (touch interrupt) | Input |
| EXIO1 | BL_EN (backlight enable) | Output |
| EXIO2 | IMU_INT1 | Input |
| EXIO3 | IMU_INT2 | Input |
| EXIO4 | RTC_INT | Input |
| EXIO5 | LCD_TE | Input |
| EXIO6 | SYS_EN | Output |
| EXIO7 | PA_EN (audio amp enable) | Output — set HIGH before audio |

---

## PlatformIO Configuration (Working)

```ini
[env:pomodoro_349]
platform = https://github.com/pioarduino/platform-espressif32/releases/download/stable/platform-espressif32.zip
board = esp32-s3-devkitc-1
framework = arduino
board_build.mcu = esp32s3
board_build.f_cpu = 240000000L
board_build.flash_size = 16MB
board_build.flash_mode = qio
board_build.arduino.memory_type = qio_opi
board_build.partitions = default_16MB.csv
board_upload.flash_size = 16MB
board_upload.maximum_size = 16777216
board_upload.speed = 921600
build_flags =
    -DBOARD_HAS_PSRAM
    -DARDUINO_USB_CDC_ON_BOOT=1
    -DARDUINO USB_MODE=1
monitor_speed = 115200
```

## Arduino IDE Settings (if using Arduino IDE)
| Setting | Value |
|---------|-------|
| Board | ESP32S3 Dev Module |
| Flash Size | 16MB (128Mb) |
| Partition Scheme | 16M Flash (3MB APP/9.9MB FATFS) |
| PSRAM | OPI PSRAM |
| USB CDC On Boot | Enabled |
| Upload Speed | 921600 |

---

## Libraries

| Library | Version | Purpose |
|---------|---------|---------|
| lvgl | 8.4.0 | UI framework |
| ArduinoJson | latest | JSON parsing for APIs |

### LVGL Install Notes
- Use lvgl8 from Waveshare repo: `Arduino_Libraries/lvgl8/lvgl`
- Copy the INNER lvgl folder, not the outer lvgl8 folder
- Copy lv_conf.h into the lvgl library folder
- Restart Arduino IDE after installing

---

## Working Reference Code
- **Display + LVGL:** `~/Documents/ESP32-S3-Touch-LCD-3.49/Examples/09_LVGL_V8_Test/`
- **Audio:** `~/Documents/rsvpnano/src/audio/AudioManager.cpp`
- **Working project:** `~/Documents/ESP32-S3-Touch-LCD-3.49/PomodoroTimer/`

---

## Critical Rules for Claude Code
- Always base display code on `09_LVGL_V8_Test` example
- Never rewrite lvgl_port.c, i2c_bsp.c, or display driver
- Only modify app-level files (main.cpp, pomodoro_timer.cpp, etc.)
- For audio issues reference rsvpnano AudioManager.cpp

---

## Flashing Instructions

### Normal Upload
```bash
~/.platformio/penv/bin/pio run -t upload
```

### If Upload Fails (No serial data received)
1. Hold BOOT button (back of board)
2. Press and release RESET button
3. Release BOOT
4. Run upload command again

### Serial Monitor
```bash
~/.platformio/penv/bin/pio device monitor
```
Port appears as `/dev/cu.usbmodemXXXX` on Mac.

---

## Known Errors and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `driver/i2c_master.h not found` | ESP-IDF version too old | Use pioarduino platform in platformio.ini |
| `LEDC_SLOW_CLK_RC_FAST undeclared` | ESP-IDF version too old | Same fix as above |
| `lvgl.h not found` | Wrong library folder copied | Copy inner lvgl folder not outer lvgl8 |
| Upload fails — no serial data | Board not in flash mode | Hold BOOT + press RESET then upload |
| Display static / not updating | Claude rewrote display driver | Revert, give working Waveshare example as base |
| Audio init succeeds but no sound | PA enable not set | Set TCA9554 EXIO7 HIGH before playing — reference rsvpnano AudioManager.cpp |
| No tasks returned from Notion | Integration not shared | Share database with integration in Notion |

---

## Projects Built on This Board

| Project | Path | Status |
|---------|------|--------|
| Pomodoro Timer | `~/Desktop/ESP32/ESP32-S3-Touch-LCD-3.49/PomodoroTimer` | Working — github.com/willtob/esp32-pomodoro |
| News Display | `~/Desktop/ESP32/ESP32-S3-Touch-LCD-3.49/NewsDisplay` | Working — github.com/willtob/esp32-news-display. Touch news reader + spoken articles; backend is `~/dev/esp-news-reporter`. **Has an LVGL desktop simulator in `sim/` — use it for UI work instead of flashing.** Read its CLAUDE.md first. |
| RSVPNano Reader | `~/Desktop/rsvpnano` | Working — needs SD card (FAT32) with books in /books/books/ |

Note: the project paths above are under `~/Desktop/ESP32/`, not `~/Desktop/`
directly. The "Working Reference Code" section further up still says
`~/Documents/...`, which is also stale — everything lives under `~/Desktop/ESP32/`.

---

## Starting a New Claude Code Session
```
Read ~/Desktop/esp32-projects/HARDWARE.md before 
we start. I want to [describe task] for my 
Waveshare ESP32-S3-Touch-LCD-3.49.
```
