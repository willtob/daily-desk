/*
 * news_ui.h — LVGL UI for the scored news digest.
 *
 * news_ui_create() is called from lvgl_port_init() while the LVGL lock is
 * held, exactly as pomodoro_ui_create() was. Everything is built there;
 * later updates happen in the 250 ms timer callback, which also runs under
 * the lock. No other module touches LVGL objects.
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

void news_ui_create(void);

/* Rebuild every view at the display's current size. Called after the display
 * rotation changes between portrait and landscape, where the layout differs
 * rather than merely moving. Must be called with the LVGL lock held — it is
 * invoked from the driver, which already holds it. */
void news_ui_relayout(void);

#ifdef __cplusplus
}
#endif
