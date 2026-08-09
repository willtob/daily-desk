#ifndef LVGL_PORT_H
#define LVGL_PORT_H

#ifdef __cplusplus
extern "C" {
#endif

/* Runtime display rotation (degrees, clockwise) */
#define DISP_ROT_0   0
#define DISP_ROT_90  90
#define DISP_ROT_180 180
#define DISP_ROT_270 270

void lvgl_port_init(void);

/* Change display rotation at runtime. Takes the LVGL lock, swaps the logical
 * resolution (portrait 172x640 <-> landscape 640x172) and invalidates.
 * Safe to call from any task. `deg` must be one of the DISP_ROT_* values. */
void lvgl_port_set_rotation(int deg);

/* Same, for callers that ALREADY hold the LVGL lock — i.e. anything running
 * inside an lv_timer or an LVGL event callback. The lock is a non-recursive
 * FreeRTOS mutex, so calling the version above from there deadlocks the
 * display task permanently. Called from imu_wake_poll(). */
void lvgl_port_set_rotation_locked(int deg);

/* Current rotation in degrees (one of DISP_ROT_*). */
int lvgl_port_get_rotation(void);

#ifdef __cplusplus
}
#endif

#endif
