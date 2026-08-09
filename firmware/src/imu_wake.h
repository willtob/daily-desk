/*
 * imu_wake.h — dim the panel until someone picks it up.
 */
#ifndef IMU_WAKE_H
#define IMU_WAKE_H

#ifdef __cplusplus
extern "C" {
#endif

/* Probes the QMI8658 and starts the accelerometer. Safe to call even if the
 * part does not answer — everything else then becomes a no-op and the panel
 * simply stays at full brightness. */
void imu_wake_init(void);

/* Poll once. Called from an LVGL timer, so it runs on the LVGL task. */
void imu_wake_poll(void);

/* Last motion metric and whether the panel is currently lit. For the temporary
 * threshold instrumentation; harmless to leave exposed. */
extern int  imu_motion;
extern bool imu_awake;

/* Set true to print the raw accelerometer axes twice a second. This is how
 * ORIENT_AXIS and ORIENT_UP_SIGN in imu_wake.cpp were established, and the
 * only way to re-establish them if the IMU is ever placed differently. */
extern bool imu_orient_trace;

#ifdef __cplusplus
}
#endif

#endif
