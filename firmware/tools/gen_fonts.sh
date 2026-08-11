#!/usr/bin/env bash
#
# Regenerate src/fonts/news_font_*.c
#
# LVGL's built-in Montserrat fonts cover ASCII only (0x20-0x7F, plus the degree
# and bullet signs). That is fine until a third of your feeds are Spanish and
# Catalan: "miércoles", "Peñíscola", "¿por qué?" and every typographic quote in
# an LLM-written summary render as empty boxes on the panel.
#
# So we build our own Montserrat at the four sizes the UI uses, over a wider
# range, and keep the FontAwesome glyphs LVGL's LV_SYMBOL_* macros point at —
# drop those and the LISTEN / BACK / STOP buttons lose their icons.
#
# Recipe follows lvgl/scripts/built_in_font/built_in_font_gen.py: same source
# faces, same bpp, same symbol list, same --force-fast-kern-format. Only the
# text range is ours.
#
# Requires: npm install -g lv_font_conv
#
# Usage:  ./tools/gen_fonts.sh
set -euo pipefail

LVGL_ROOT="${LVGL_ROOT:-/Users/william.tobin/Desktop/ESP32/ESP32-S3-Touch-LCD-3.49/Arduino_Libraries/lvgl8}"
FONT_DIR="$LVGL_ROOT/lvgl/scripts/built_in_font"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/src/fonts"

TEXT_FACE="$FONT_DIR/Montserrat-Medium.ttf"
SYM_FACE="$FONT_DIR/FontAwesome5-Solid+Brands+Regular.woff"

# 0x20-0x7F   ASCII
# 0xA0-0xFF   Latin-1 Supplement — the whole point: áéíóúüñç¿¡ and their capitals,
#             plus à è ò for Catalan, · for the Catalan l·l, and ° which LVGL's
#             own build includes separately.
# 0x2013,14   en dash, em dash
# 0x2018,19   curly single quotes — U+2019 is the apostrophe the model writes in
# 0x201C,1D   curly double quotes      "Spain's", so this one is not optional
# 0x2022      bullet (LVGL's default range includes it)
# 0x2026      ellipsis
# 0x20AC      euro — Spanish price figures
TEXT_RANGE='0x20-0x7F,0xA0-0xFF,0x2013,0x2014,0x2018,0x2019,0x201C,0x201D,0x2022,0x2026,0x20AC'

# Verbatim from built_in_font_gen.py — the codepoints behind LV_SYMBOL_*.
SYMS="61441,61448,61451,61452,61452,61453,61457,61459,61461,61465,61468,61473,61478,61479,61480,61502,61507,61512,61515,61516,61517,61521,61522,61523,61524,61543,61544,61550,61552,61553,61556,61559,61560,61561,61563,61587,61589,61636,61637,61639,61641,61664,61671,61674,61683,61724,61732,61787,61931,62016,62017,62018,62019,62020,62087,62099,62212,62189,62810,63426,63650"

command -v lv_font_conv >/dev/null || {
    echo "lv_font_conv not found — npm install -g lv_font_conv" >&2; exit 1; }
[ -f "$TEXT_FACE" ] || { echo "missing $TEXT_FACE — check LVGL_ROOT" >&2; exit 1; }

mkdir -p "$OUT_DIR"
for size in 12 14 16 20; do
    out="$OUT_DIR/news_font_${size}.c"
    echo "  news_font_${size}"
    lv_font_conv \
        --no-compress --no-prefilter --bpp 4 --size "$size" \
        --font "$TEXT_FACE" -r "$TEXT_RANGE" \
        --font "$SYM_FACE"  -r "$SYMS" \
        --format lvgl -o "$out" --force-fast-kern-format
done
echo "wrote $OUT_DIR/news_font_{12,14,16,20}.c"
