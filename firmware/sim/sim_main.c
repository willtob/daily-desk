/*
 * sim_main.c — runs news_ui.cpp on the desktop against SDL2.
 *
 * Same UI source, same lv_conf.h, same 172x640 geometry as the board, so what
 * you see here is what the panel shows. LVGL 8 has no built-in SDL backend and
 * the separate lv_drivers repo is more than this needs, so the display flush
 * and pointer input are implemented directly below — about 60 lines.
 *
 *   ./sim              open a window; mouse is the touchscreen, B = BOOT button
 *   ./sim --shot F.bmp render a few frames headlessly, write F.bmp, exit
 *   ./sim --shot F.bmp --boot   tap BOOT first (deck: manual refresh)
 *
 * --swipe takes up|down|left|right and may be repeated, which is how a
 * headless shot reaches a view that is only a gesture away: the deck flips on
 * left/right, swipe up opens the list, swipe right leaves it again.
 *
 * --swipe, --tap, --scroll and --boot are applied in the order written, so
 * one invocation can drive a whole path:
 *
 *   ./sim --shot out.bmp --swipe up --tap 200 --swipe right
 *       list -> open its second card -> swipe back, which must land on the
 *       list rather than on the deck.
 *
 * The screenshot mode is the point: it makes the UI reviewable without
 * physically looking at the board.
 */
#include <SDL2/SDL.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "lvgl.h"
#include "user_config.h"
#include "news_ui.h"
#include "news_client.h"

#define SCALE 2   /* 172x640 is tiny on a retina display; draw it at 2x */

int sim_boot_button_down = 0;      /* read by the Arduino.h shim */

static SDL_Window   *window;
static SDL_Renderer *renderer;
static SDL_Texture  *texture;
static uint32_t     *framebuf;     /* ARGB8888, EXAMPLE_LCD_H_RES x V_RES */

static lv_disp_draw_buf_t draw_buf;
static lv_color_t buf1[EXAMPLE_LCD_H_RES * 80];

/* LVGL hands us 16-bit colour (LV_COLOR_DEPTH 16, matching the board); widen
 * it to ARGB8888 for SDL. */
static void disp_flush(lv_disp_drv_t *drv, const lv_area_t *area, lv_color_t *px)
{
    for (int y = area->y1; y <= area->y2; y++) {
        for (int x = area->x1; x <= area->x2; x++) {
            /* lv_color_to32 rather than poking at ch.red/green/blue: the 16-bit
             * struct splits green when LV_COLOR_16_SWAP is set. */
            framebuf[y * EXAMPLE_LCD_H_RES + x] = lv_color_to32(*px++);
        }
    }
    lv_disp_flush_ready(drv);
}

/* Headless tap injection, so screenshot mode can reach screens that are only
 * available after a touch (the detail view). Overrides the mouse when active. */
static int inject_active = 0, inject_x = 0, inject_y = 0, inject_pressed = 0;

static void mouse_read(lv_indev_drv_t *drv, lv_indev_data_t *data)
{
    (void)drv;
    if (inject_active) {
        data->point.x = inject_x;
        data->point.y = inject_y;
        data->state = inject_pressed ? LV_INDEV_STATE_PRESSED
                                     : LV_INDEV_STATE_RELEASED;
        return;
    }
    int x, y;
    uint32_t buttons = SDL_GetMouseState(&x, &y);
    data->point.x = x / SCALE;
    data->point.y = y / SCALE;
    data->state = (buttons & SDL_BUTTON(SDL_BUTTON_LEFT)) ? LV_INDEV_STATE_PRESSED
                                                          : LV_INDEV_STATE_RELEASED;
}

static void pump(int frames)
{
    for (int i = 0; i < frames; i++) { lv_tick_inc(20); lv_timer_handler(); }
}

/* Press and release at a point, then let animations settle. */
static void inject_tap(int x, int y)
{
    inject_active = 1; inject_x = x; inject_y = y;
    inject_pressed = 1; pump(3);
    inject_pressed = 0; pump(3);
    inject_active = 0;
    pump(20);            /* 400 ms — covers the 180 ms slide */
}

/* Drag from (x0,y0) to (x1,y1), so swipe gestures can be exercised headlessly.
 * LVGL needs the movement spread over several reads to classify it as a
 * gesture rather than a click. Vertical drags matter now that swipe-up opens
 * the list and swipe-down leaves it. */
static void inject_drag(int x0, int y0, int x1, int y1)
{
    inject_active = 1;
    inject_x = x0; inject_y = y0; inject_pressed = 1; pump(2);
    for (int i = 1; i <= 8; i++) {
        inject_x = x0 + (x1 - x0) * i / 8;
        inject_y = y0 + (y1 - y0) * i / 8;
        pump(1);
    }
    inject_pressed = 0; pump(3);
    inject_active = 0;
    pump(20);
}

static void present(void)
{
    SDL_UpdateTexture(texture, NULL, framebuf,
                      EXAMPLE_LCD_H_RES * (int)sizeof(uint32_t));
    SDL_RenderClear(renderer);
    SDL_RenderCopy(renderer, texture, NULL, NULL);
    SDL_RenderPresent(renderer);
}

/* What the UI actually costs out of LV_MEM_SIZE.
 *
 * Worth having as a real flag rather than a debugging afterthought: LVGL's
 * out-of-memory behaviour is LV_USE_ASSERT_MALLOC's `while(1)`, so exhausting
 * the pool does not report an error, it hangs — on the device with the
 * backlight already on, which looks exactly like a driver problem. Checking
 * the number here is much cheaper than diagnosing that on the panel. */
static void report_mem(void)
{
    lv_mem_monitor_t m;
    lv_mem_monitor(&m);
    printf("[sim] lv_mem %u/%u bytes used (%u%%), largest free block %u\n",
           (unsigned)(m.total_size - m.free_size), (unsigned)m.total_size,
           (unsigned)m.used_pct, (unsigned)m.free_biggest_size);
}

/* One scripted input, in command-line order. */
typedef struct {
    char        kind;   /* 's' swipe, 't' tap, 'c' scroll, 'b' BOOT */
    int         n;      /* tap y, or scroll offset */
    int         x;      /* tap x; -1 means centre */
    const char *s;      /* swipe direction */
} ACTIONS_T;

static void save_bmp(const char *path)
{
    SDL_Surface *s = SDL_CreateRGBSurfaceFrom(
        framebuf, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES, 32,
        EXAMPLE_LCD_H_RES * (int)sizeof(uint32_t),
        0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000);
    if (!s) { fprintf(stderr, "surface failed: %s\n", SDL_GetError()); return; }
    if (SDL_SaveBMP(s, path) != 0) fprintf(stderr, "save failed: %s\n", SDL_GetError());
    else printf("[sim] wrote %s (%dx%d)\n", path, EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES);
    SDL_FreeSurface(s);
}

int main(int argc, char **argv)
{
    const char *shot = NULL;
    int mem = 0;
    /* Actions run in the order they are written on the command line, so a
     * single shot can express a real sequence: --swipe up --tap 200 is "open
     * the list, then tap the second card in it". Grouping them by kind
     * instead — all swipes, then all taps — silently turns that into "open
     * the list, come back, then tap the deck", which produces a perfectly
     * plausible screenshot of the wrong thing. */
    ACTIONS_T act[16];
    int act_n = 0;
    for (int i = 1; i < argc && act_n < 16; i++) {
        if      (!strcmp(argv[i], "--shot") && i + 1 < argc) shot = argv[++i];
        else if (!strcmp(argv[i], "--mem"))                  mem  = 1;
        else if (!strcmp(argv[i], "--boot")) {
            act[act_n].kind = 'b'; act_n++;
        }
        else if (!strcmp(argv[i], "--scroll") && i + 1 < argc) {
            act[act_n].kind = 'c'; act[act_n].n = atoi(argv[++i]); act_n++;
        }
        else if (!strcmp(argv[i], "--tap") && i + 1 < argc) {
            /* "y" taps the middle of the row, "x,y" picks the column too —
             * needed for the nav bar, whose buttons sit at the edges where a
             * centre tap only ever lands on the counter between them. */
            const char *v = argv[++i];
            const char *comma = strchr(v, ',');
            act[act_n].kind = 't';
            act[act_n].x    = comma ? atoi(v) : -1;
            act[act_n].n    = comma ? atoi(comma + 1) : atoi(v);
            act_n++;
        }
        else if (!strcmp(argv[i], "--swipe") && i + 1 < argc) {
            act[act_n].kind = 's'; act[act_n].s = argv[++i]; act_n++;
        }
    }

    if (SDL_Init(SDL_INIT_VIDEO) != 0) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    window = SDL_CreateWindow("NewsDisplay 172x640",
                              SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                              EXAMPLE_LCD_H_RES * SCALE, EXAMPLE_LCD_V_RES * SCALE,
                              shot ? SDL_WINDOW_HIDDEN : SDL_WINDOW_SHOWN);
    renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    texture  = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_ARGB8888,
                                 SDL_TEXTUREACCESS_STREAMING,
                                 EXAMPLE_LCD_H_RES, EXAMPLE_LCD_V_RES);
    framebuf = calloc((size_t)EXAMPLE_LCD_H_RES * EXAMPLE_LCD_V_RES, sizeof(uint32_t));

    lv_init();
    lv_disp_draw_buf_init(&draw_buf, buf1, NULL, EXAMPLE_LCD_H_RES * 80);

    static lv_disp_drv_t disp_drv;
    lv_disp_drv_init(&disp_drv);
    disp_drv.draw_buf  = &draw_buf;
    disp_drv.flush_cb  = disp_flush;
    disp_drv.hor_res   = EXAMPLE_LCD_H_RES;
    disp_drv.ver_res   = EXAMPLE_LCD_V_RES;
    lv_disp_drv_register(&disp_drv);

    static lv_indev_drv_t indev_drv;
    lv_indev_drv_init(&indev_drv);
    indev_drv.type    = LV_INDEV_TYPE_POINTER;
    indev_drv.read_cb = mouse_read;
    lv_indev_drv_register(&indev_drv);

    news_client_init();      /* fills the sample articles */
    news_ui_create();        /* the real UI, unmodified */

    if (shot) {
        /* Let the 250 ms refresh callback run at least once so the deck is
         * populated, then replay the scripted inputs in order. */
        pump(60);

        for (int i = 0; i < act_n; i++) {
            switch (act[i].kind) {
            case 's':
                /* Horizontal drags run at y=300 (mid-card); vertical ones at
                 * x=86. Both start on something clickable, which is what LVGL
                 * hit-tests to decide whose gesture this is. */
                if      (!strcmp(act[i].s, "right")) inject_drag(20, 300, 150, 300);
                else if (!strcmp(act[i].s, "left"))  inject_drag(150, 300, 20, 300);
                else if (!strcmp(act[i].s, "up"))    inject_drag(86, 430, 86, 140);
                else if (!strcmp(act[i].s, "down"))  inject_drag(86, 140, 86, 430);
                else fprintf(stderr, "[sim] unknown --swipe %s\n", act[i].s);
                break;

            case 'c': {
                lv_obj_t *scr  = lv_scr_act();
                lv_obj_t *cont = lv_obj_get_child(scr, 1);      /* cont_list */
                lv_obj_t *body = lv_obj_get_child(cont, 2);     /* list_body */
                lv_obj_scroll_to_y(body, act[i].n, LV_ANIM_OFF);
                pump(10);
                break;
            }

            case 't':
                inject_tap(act[i].x >= 0 ? act[i].x : EXAMPLE_LCD_H_RES / 2,
                           act[i].n);
                break;

            case 'b':
                /* The UI polls BOOT from the 250 ms timer, so the press has to
                 * be held across several pumps to be seen at all; the frames
                 * after release are what put "refreshing..." on screen. */
                sim_boot_button_down = 1;
                pump(20);
                sim_boot_button_down = 0;
                pump(20);
                break;
            }
        }
        save_bmp(shot);
        if (mem) report_mem();
        return 0;
    }

    bool run = true;
    uint32_t last = SDL_GetTicks();
    while (run) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) run = false;
            if (e.type == SDL_KEYDOWN || e.type == SDL_KEYUP) {
                if (e.key.keysym.sym == SDLK_b)
                    sim_boot_button_down = (e.type == SDL_KEYDOWN);
                if (e.key.keysym.sym == SDLK_ESCAPE) run = false;
                if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_s)
                    save_bmp("shot.bmp");
            }
        }
        uint32_t now = SDL_GetTicks();
        lv_tick_inc(now - last);
        last = now;
        lv_timer_handler();
        present();
        SDL_Delay(5);
    }

    SDL_Quit();
    return 0;
}
