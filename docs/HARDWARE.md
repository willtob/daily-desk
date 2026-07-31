# Board reference — Waveshare ESP32-S3-Touch-LCD-3.49

**Scope: the hardware constants `../firmware/` is built against.** Pin numbers
and I2C addresses are physical facts about the board, so this file can't drift
out of date the way a second copy of the full notes would.

Everything else about the board — flashing workflow, LVGL install notes, known
errors and fixes, the other projects using it — lives in the canonical notes at
`~/Desktop/ESP32/esp32-projects/HARDWARE.md`, shared with the PomodoroTimer,
NotionDisplay and RSVPNano projects. **That file is the original; don't copy it
back here.** This project's own workflow rules are in
[`../firmware/CLAUDE.md`](../firmware/CLAUDE.md).

- Waveshare wiki: waveshare.com/wiki/ESP32-S3-Touch-LCD-3.49
- Waveshare GitHub: github.com/waveshareteam/ESP32-S3-Touch-LCD-3.49

## Board identity

- **Model:** Waveshare ESP32-S3-Touch-LCD-3.49
- **Chip:** ESP32-S3R8 — 240 MHz, 8 MB OPI PSRAM, 16 MB QIO flash

## Pin assignments

| Peripheral | GPIO / address | Notes |
|---|---|---|
| Display | SPI3, CS=9, CLK=10, D0=11, D1=12, D2=13, D3=14, RST=21 | AXS15231B, QSPI, 172×640 |
| Backlight | GPIO 8 | LEDC channel 1 / timer 3, 8-bit PWM |
| Touch | I2C port 1, SDA=17, SCL=18, addr 0x3B | Capacitive, AXS15231B |
| Main I2C bus | I2C port 0, SDA=47, SCL=48 | Shared by RTC, IMU, audio, I/O expander |
| Audio DAC | I2C addr 0x18 (ES8311) | I2S: MCLK=7, BCLK=15, WS=46, DOUT=6 |
| Audio ADC | ES7210 | Dual microphone array |
| PA enable | I2C addr 0x20 (TCA9554), EXIO7 HIGH | Must be set before playing audio |
| RTC | I2C addr 0x51 (PCF85063) | Time retention |
| IMU | I2C addr 0x6B (QMI8658) | 3-axis gyro + 3-axis accelerometer |
| TCA9554 expander | I2C addr 0x20, INT=IO42 | Backlight, PA, IMU ints, RTC int |
| SD card | CS=38, MOSI=39, MISO=40, SCLK=41 | FAT32 required |
| BOOT button | GPIO 0, active-low, pull-up | Used as the user button — see `firmware/CLAUDE.md` |
| Speaker | MX1.25 2-pin header (back) | External speaker required |
| USB | Type-C (back) | Programming and serial monitor |

## TCA9554 expander map

| EXIO | Function | Direction |
|---|---|---|
| EXIO0 | TP_INT (touch interrupt) | Input |
| EXIO1 | BL_EN (backlight enable) | Output |
| EXIO2 | IMU_INT1 | Input |
| EXIO3 | IMU_INT2 | Input |
| EXIO4 | RTC_INT | Input |
| EXIO5 | LCD_TE | Input |
| EXIO6 | SYS_EN | Output |
| EXIO7 | PA_EN (audio amp enable) | Output — HIGH before audio |

The build settings that go with these — flash mode, PSRAM type, partition table
— aren't written down twice either: they live in
[`../firmware/platformio.ini`](../firmware/platformio.ini), which is what
actually builds.
