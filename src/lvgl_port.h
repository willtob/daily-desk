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
 * resolution (portrait 172x640 <-> landscape 640x172) and rebuilds the UI.
 * Safe to call from any task. `deg` must be one of the DISP_ROT_* values. */
void lvgl_port_set_rotation(int deg);

/* Current rotation in degrees (one of DISP_ROT_*). */
int lvgl_port_get_rotation(void);

#ifdef __cplusplus
}
#endif

#endif
