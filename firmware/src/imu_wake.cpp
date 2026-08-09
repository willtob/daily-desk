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
#include "lvgl_port.h"

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

/* ── Which way up ─────────────────────────────────────────────────────────
 *
 * The same gravity vector that is useless for detecting motion is exactly
 * what tells you which way up the panel is: at rest it points down, so its
 * component along the panel's long edge changes sign when the board is turned
 * over. No gyro needed — a gyro measures rotation *rate*, which is zero once
 * you have finished turning it, and orientation is a question about where it
 * has settled.
 *
 * **All four orientations are wired up, and they are not equally cheap.**
 * A portrait-to-portrait flip costs nothing but the transform already running
 * in the flush callback — same logical resolution, same widgets. Turning onto
 * the side is a different thing entirely: 640x172 is a second layout, so the
 * whole widget tree is torn down and rebuilt by news_ui_relayout(). That is
 * why the dwell below matters more than it looks. Every spurious landscape
 * decision is a full rebuild, not a redraw.
 *
 * ── Two constants, both measured on the board ────────────────────────────
 *
 * ORIENT_AXIS is which raw accelerometer axis runs along the panel's long
 * (640 px) edge; ORIENT_AXIS_SHORT is the 172 px edge. Z is the screen normal
 * and is never consulted — it only ever says how flat the panel is.
 *
 * **X is the long edge and Y is the short edge**, and getting this backwards
 * is worth understanding because the wrong answer looked well-supported.
 *
 * The tempting reasoning: gravity reads a full 1 g on Y whenever the board is
 * left alone (ay -16713 over two long captures), the display is portrait, so
 * Y must be the vertical/long axis. That inference has an unstated premise —
 * that the board rests in portrait — and the premise was false.
 *
 * What settled it was a behavioural observation, not a capture: holding the
 * board *horizontal* and turning it 180 flipped the display, back when only
 * the Y axis could trigger a flip. If Y were the long edge, holding the board
 * horizontal puts ay at ~0 and nothing could have flipped. It flipped, so Y is
 * vertical in that position, so Y is the SHORT edge.
 *
 * The lesson for the next person: a static capture cannot tell you which axis
 * is which unless you already know how the board was oriented when it was
 * taken. Two positions that agree with each other can still agree about the
 * wrong thing. Prefer a test where the *display* reacts, because that closes
 * the loop through the panel instead of stopping at the sensor. */
#define ORIENT_AXIS       0       /* X — the 640 px edge */
#define ORIENT_UP_SIGN   (-1)

/* The other in-plane axis: Y, the 172 px edge. Gravity lands here instead when
 * the board is on its side, and its sign says which side. */
#define ORIENT_AXIS_SHORT  1

/* Which landscape a positive short-axis reading means. Confirmed on the panel,
 * which is the only place it can be: the capture separates landscape-left from
 * landscape-right, but nothing in the sensor data says which one the driver
 * calls 90 — that is a fact about how the panel is mounted.
 *
 * Worth knowing that this is a safe thing to get wrong and a cheap thing to
 * fix: the two flush_cb transforms are an exact 180 pair (substituting
 * (639-j, 171-i) into the ROT_90 mapping yields ROT_270), so a wrong choice
 * here can only ever invert landscape, never skew it, and swapping the two is
 * always the whole fix. Portrait is unaffected either way. */
#define ORIENT_LAND_POS   DISP_ROT_270
#define ORIENT_LAND_NEG   DISP_ROT_90

/* How much the winning axis must beat the other by, x10. Turning the board
 * sweeps through 45 degrees where both read ~0.7 g, and without a margin the
 * decision oscillates between portrait and landscape on sensor noise for the
 * whole of that sweep. 13 = the winner must lead by 30%. */
#define ORIENT_RATIO   13

/* 1 g in LSB. CTRL2 selects the +/-2 g range, so full scale 32768 = 2 g. */
#define ONE_G   16384

/* How much gravity has to lie along the long edge before the reading means
 * anything. Lying flat, gravity is nearly all on the screen normal and the
 * in-plane component is small and points in an arbitrary direction — without
 * a deadband a panel resting face-up would pick a rotation out of noise.
 *
 * A quarter g, i.e. about 15 degrees up from flat. Chosen against the measured
 * resting value of 3900 (0.24 g), which lands just below it: sitting on its
 * stand, the panel has no opinion and holds whatever is on screen.
 *
 * Sitting near the threshold is safe, which is what makes a low value
 * defensible here. Crossing it does not cause a flip — it only lets the panel
 * *agree with the rotation it already has*. Flipping requires a reading past
 * the threshold with the OPPOSITE sign, which needs the board physically
 * turned over. So the threshold trades responsiveness against nothing worse
 * than inertia, and the failure mode of setting it too low is a flip when you
 * tilt a flat panel, not a flip at rest.
 *
 * Raise it toward ONE_G/2 to demand a more deliberate gesture; lower it to
 * ~2500 to make the flip work while the panel is still on its stand. */
#define ORIENT_MIN   (ONE_G / 4)

/* And how long it has to stay that way. Turning the board over sweeps through
 * every angle including the wrong one, so acting on the first sample that
 * crosses zero would flip the display mid-turn and possibly back again. At a
 * 100 ms poll this is seven consecutive agreeing samples. */
#define ORIENT_HOLD_MS   700

int  imu_motion = 0;
bool imu_awake  = true;
bool imu_orient_trace = false;

/* Applied rotation, candidate rotation, and when the candidate first appeared.
 * Seeded to the driver's boot rotation so the first poll agrees with what is
 * already on screen and nothing flips at startup. */
static int      orient_rot  = DISP_ROT_180;
static int      orient_cand = DISP_ROT_180;
static uint32_t orient_since = 0;
static uint32_t orient_last_trace = 0;

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

/* Decide which way up the panel is and flip the display if it has settled the
 * other way. Runs on the LVGL task, under the LVGL lock, which is why it can
 * only use the _locked form of the rotation call. */
static void orient_update(const int16_t now[3])
{
    int32_t lng = (int32_t)now[ORIENT_AXIS]       * ORIENT_UP_SIGN;
    int32_t sht = (int32_t)now[ORIENT_AXIS_SHORT];
    int32_t alng = lng < 0 ? -lng : lng;
    int32_t asht = sht < 0 ? -sht : sht;

    if (imu_orient_trace && millis() - orient_last_trace > 500) {
        orient_last_trace = millis();
        Serial.printf("[orient] ax=%6d ay=%6d az=%6d  lng=%6d sht=%6d  rot=%d\n",
                      now[0], now[1], now[2], (int)lng, (int)sht, orient_rot);
    }

    /* Whichever in-plane axis carries the most gravity decides, provided it
     * carries enough of it and clearly beats the other. Failing either test
     * means no opinion: the panel is lying too flat to say, or it is being
     * held at a diagonal on the way between two orientations.
     *
     * No opinion holds whatever is on screen — deliberately not "revert to
     * default", because setting the panel down on a desk must not undo the
     * rotation you just made. */
    int want;
    if (alng >= ORIENT_MIN && alng * 10 > asht * ORIENT_RATIO) {
        want = (lng > 0) ? DISP_ROT_180 : DISP_ROT_0;
    } else if (asht >= ORIENT_MIN && asht * 10 > alng * ORIENT_RATIO) {
        want = (sht > 0) ? ORIENT_LAND_POS : ORIENT_LAND_NEG;
    } else {
        orient_cand  = orient_rot;
        orient_since = millis();
        return;
    }

    if (want != orient_cand) {
        orient_cand  = want;
        orient_since = millis();
        return;
    }

    if (want != orient_rot && millis() - orient_since >= ORIENT_HOLD_MS) {
        orient_rot = want;
        Serial.printf("[orient] flip -> %d\n", want);
        lvgl_port_set_rotation_locked(want);
    }
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

        orient_update(now);
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
