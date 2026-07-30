/*
 * Arduino.h — desktop shim for the simulator.
 *
 * news_ui.cpp uses only the GPIO read path (for the BOOT button). Everything
 * else here exists so the same source compiles unmodified on the host.
 */
#pragma once
#include <stdint.h>
#include <stdio.h>

#define INPUT          0
#define INPUT_PULLUP   2
#define OUTPUT         1
#define LOW            0
#define HIGH           1
#define GPIO_NUM_0     0

#ifdef __cplusplus
extern "C" {
#endif

/* The simulator maps the BOOT button to a keyboard key; sim_main.c owns this. */
extern int sim_boot_button_down;

static inline void pinMode(int pin, int mode) { (void)pin; (void)mode; }
static inline int  digitalRead(int pin)
{
    (void)pin;
    return sim_boot_button_down ? LOW : HIGH;   /* active-low, like the board */
}
static inline void delay(unsigned long ms) { (void)ms; }

#ifdef __cplusplus
}

/* Minimal Serial so any stray logging in shared code still builds. */
struct SimSerial {
    void begin(unsigned long) {}
    template <typename... A> void printf(const char *f, A... a) { ::printf(f, a...); }
    void println(const char *s) { ::printf("%s\n", s); }
    void print(const char *s) { ::printf("%s", s); }
};
static SimSerial Serial;
#endif
