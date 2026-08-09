/*
 * imu_wake.cpp — the panel sits dim, and lights when you pick it up.
 *
 * A news display that is bright all day is a lamp. Dimming it until it is
 * wanted is what makes it furniture, and the board has both halves needed to
 * do it properly:
 *
 *   * a **QMI8658** 6-axis IMU at 0x6B on I2C port 0, already registered as
 *     `imu_dev_handle` by i2c_bsp.c — no new bus, no new device setup;
 *   * a **real 8-bit PWM backlight** on GPIO 8 (LEDC channel 1, timer 3), so
 *     this is a smooth ramp rather than a switch.
 *
 * ── Three things about this board that shape the code ─────────────────────
 *
 * **The backlight duty is inverted.** lcd_bl_pwm_bsp.h defines its levels as
 * `0xff - n`, so LCD_PWM_MODE_255 — the brightest, and what main.cpp boots
 * with — is duty 0, and 255 is off. Passing a percentage straight to
 * setUpduty() gets you a panel that goes dark when you ask for bright.
 *
 * **Touch is on I2C port 1, the IMU on port 0.** They cannot collide. That is
 * worth knowing because the obvious worry with adding a second I2C reader to
 * this project is the touch controller, and here there is nothing to guard.
 * Polling still happens on the LVGL task via an lv_timer, so it is serialised
 * with everything else that touches a device anyway.
 *
 * **Gravity is always there.** An accelerometer at rest reads ~1g, so absolute
 * magnitude says nothing about whether the thing moved. What moves is the
 * *vector*: tilt it and gravity redistributes across the axes even if the
 * total stays 1g. So the metric is the change between consecutive samples,
 * summed over the three axes — an L1 norm, because it needs no square root and
 * the threshold is empirical anyway.
 *
 * ── Idleness ──────────────────────────────────────────────────────────────
 *
 * Motion is only half of "in use". LVGL already tracks the other half:
 * lv_disp_get_inactive_time() is the time since the last input event, so touch
 * and the BOOT button keep the panel lit without this module having to know
 * anything about them. Reading a long article without moving the device does
 * not dim it, because scrolling is input.
 */
#include <Arduino.h>
#include <math.h>

#include "imu_wake.h"
#include "i2c_bsp.h"
#include "news_audio.h"
#include "drv/lcd_bl_bsp/lcd_bl_pwm_bsp.h"
#include "lvgl.h"

/* ── QMI8658 registers ─────────────────────────────────────────────────
 * Values checked against SensorLib's QMI8658Constants.h rather than recalled;
 * the accelerometer block is all this needs. */
#define QMI_WHO_AM_I   0x00
#define QMI_CTRL1      0x02
#define QMI_CTRL2      0x03
#define QMI_CTRL7      0x08
#define QMI_AX_L       0x35

#define QMI_WHO_AM_I_VALUE  0x05

/* ── Brightness ───────────────────────────────────────────────────────
 * Percentages, converted at the point of use. See the note on inversion. */
#define BL_FULL_PCT   100
#define BL_DIM_PCT     12   /* readable in a dim room, invisible in a bright one */

/* How long without motion *and* without a touch before it settles down. */
#define IDLE_MS     25000

/* Milliseconds per ramp step, and how much brightness moves each time. The
 * ramp is what stops it looking like a fault: a panel that snaps to 12% reads
 * as a glitch, one that slides there reads as deliberate. */
#define RAMP_STEP_PCT   4

/* Motion metric above which the panel wakes. Measured, both halves.
 *
 *   at rest on a desk   25-145 typical, worst observed spike 345
 *   being picked up     p25 523, median 4243, p75 9380, peak 32068
 *
 * The useful discovery is that the distribution is **bimodal**: the thing is
 * either being moved, which reads in the thousands, or it is still, which
 * reads in the tens. Almost nothing lands between 300 and 1200. So the exact
 * threshold barely matters — 500 catches 75% of pick-up samples and 1200
 * catches 68% — and the sensible move is to take the largest margin that costs
 * nothing, rather than the lowest number that works.
 *
 * 1200 is 3.5x the worst resting spike. At 68% of samples crossing, any real
 * handling trips it inside one or two 100 ms polls.
 *
 * The 25% of pick-up samples that fall *below* the threshold are the moments
 * the panel is being held steady, which read exactly like the desk. They do
 * not matter, because waking keys off the last-motion timestamp rather than
 * the instantaneous value: staying still for a second while holding it cannot
 * re-dim something that needs IDLE_MS of stillness. */
#define WAKE_MOTION   1200

int  imu_motion = 0;
bool imu_awake  = true;

static bool     present     = false;
static int16_t  prev[3]     = { 0, 0, 0 };
static bool     have_prev   = false;
static uint32_t last_motion = 0;
static int      cur_pct     = BL_FULL_PCT;
static int      want_pct    = BL_FULL_PCT;

/* The driver's levels are `0xff - n`, so this inverts. Clamped because a duty
 * outside 0..255 wraps rather than saturating. */
static void backlight_set(int pct)
{
    if (pct < 0)   pct = 0;
    if (pct > 100) pct = 100;
    setUpduty((uint16_t)(0xff - (pct * 255 / 100)));
}

static bool qmi_read(uint8_t reg, uint8_t *buf, uint8_t len)
{
    return i2c_read_buff(imu_dev_handle, reg, buf, len) == 0;
}

static bool qmi_write(uint8_t reg, uint8_t val)
{
    uint8_t v = val;
    return i2c_write_buff(imu_dev_handle, reg, &v, 1) == 0;
}

void imu_wake_init(void)
{
    uint8_t who = 0;

    if (!qmi_read(QMI_WHO_AM_I, &who, 1) || who != QMI_WHO_AM_I_VALUE) {
        /* No IMU, or it did not answer. Leave the panel bright and say so
         * once — a display that silently stopped dimming is a much more
         * confusing thing to debug than one that never started. */
        Serial.printf("[imu] QMI8658 not found (WHO_AM_I 0x%02X) — auto-dim off\n", who);
        return;
    }

    /* CTRL1: address auto-increment, so one read gets all six accel bytes.
     * CTRL2: ±2 g at 235 Hz. The range matters more than the rate here — a
     *        narrow range gives finer counts, and finer counts mean the
     *        difference between "picked up" and "someone walked past" is
     *        larger relative to the noise.
     * CTRL7: accelerometer only. The gyro is the power-hungry half and adds
     *        nothing: rotation without translation is not being picked up. */
    qmi_write(QMI_CTRL1, 0x40);
    qmi_write(QMI_CTRL2, 0x03);
    qmi_write(QMI_CTRL7, 0x01);

    present     = true;
    last_motion = millis();
    Serial.println("[imu] QMI8658 ready — panel dims when idle");
}

void imu_wake_poll(void)
{
    if (!present) return;

    uint8_t raw[6];
    if (qmi_read(QMI_AX_L, raw, sizeof(raw))) {
        int16_t now[3];
        for (int i = 0; i < 3; i++) {
            now[i] = (int16_t)((uint16_t)raw[i * 2] | ((uint16_t)raw[i * 2 + 1] << 8));
        }

        if (have_prev) {
            imu_motion = abs(now[0] - prev[0])
                       + abs(now[1] - prev[1])
                       + abs(now[2] - prev[2]);
            if (imu_motion > WAKE_MOTION) last_motion = millis();
        }
        memcpy(prev, now, sizeof(prev));
        have_prev = true;
    }

    /* Input counts as use, so reading a long story without moving the panel
     * does not dim it. Narration counts too — the screen going dark halfway
     * through an article you are listening to reads as a fault. */
    uint32_t idle = millis() - last_motion;
    if (lv_disp_get_inactive_time(NULL) < idle) idle = lv_disp_get_inactive_time(NULL);

    imu_awake = (idle < IDLE_MS) || news_audio_playing;
    want_pct  = imu_awake ? BL_FULL_PCT : BL_DIM_PCT;

    if (cur_pct != want_pct) {
        int step = (want_pct > cur_pct) ? RAMP_STEP_PCT : -RAMP_STEP_PCT;
        cur_pct += step;
        if ((step > 0 && cur_pct > want_pct) || (step < 0 && cur_pct < want_pct)) {
            cur_pct = want_pct;
        }
        backlight_set(cur_pct);
    }
}
