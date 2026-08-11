/*
 * news_fonts.h — Montserrat at the UI's four sizes, over a range that actually
 * covers what the digest contains.
 *
 * LVGL's built-in lv_font_montserrat_* are ASCII-only. About a third of the
 * feeds are Spanish and Catalan, and since Phase 9 every one of those articles
 * carries ~800 characters of accented prose rather than a headline that might
 * happen to dodge the issue — "miércoles", "Peñíscola", "¿por qué?" all came
 * out as empty boxes on the panel. The LLM also writes typographic quotes, so
 * U+2019 turned every "Spain's" into a box too.
 *
 * Regenerate with ./tools/gen_fonts.sh, which documents the exact ranges.
 * The FontAwesome glyphs behind LV_SYMBOL_* are included, so LISTEN / BACK /
 * STOP keep their icons.
 */
#pragma once

#ifdef LV_LVGL_H_INCLUDE_SIMPLE
#include "lvgl.h"
#else
#include "lvgl/lvgl.h"
#endif

#ifdef __cplusplus
extern "C" {
#endif

LV_FONT_DECLARE(news_font_12);
LV_FONT_DECLARE(news_font_14);
LV_FONT_DECLARE(news_font_16);
LV_FONT_DECLARE(news_font_20);

#ifdef __cplusplus
}
#endif
