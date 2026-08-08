# LVGL simulator for NewsDisplay.
#
#   make          build ./sim
#   make run      build and open the window
#   make shot     render one frame to shot.bmp without opening a window
#
# Builds the real src/news_ui.cpp against the real include/lv_conf.h, so fonts,
# colour depth and geometry match the board exactly. Firmware modules the UI
# calls into (news_client, news_audio, wifi_manager) are stubbed in stubs/,
# against their real headers so the sim can't drift from the device API.

# Absolute paths on purpose. With relative ones the object paths below expand
# to build/../../... which escapes the build directory and scatters .o files
# through the source tree — including a junk copy of Arduino_Libraries/.
PROJ    := $(abspath ..)
# The one path this build takes from outside the repo — see the [lvgl] section
# of ../platformio.ini, which holds the device build's copy of it.
# Overridable: make LVGL_ROOT=/path/to/Arduino_Libraries/lvgl8
LVGL_ROOT ?= /Users/william.tobin/Desktop/ESP32/ESP32-S3-Touch-LCD-3.49/Arduino_Libraries/lvgl8
LVGL    := $(LVGL_ROOT)/lvgl
SDL_PFX := /opt/homebrew

CC      := cc
CXX     := c++

INCLUDES := -I. -Istubs -I$(PROJ)/include -I$(PROJ)/src -I$(LVGL) -I$(SDL_PFX)/include
# LV_LVGL_H_INCLUDE_SIMPLE: lv_font_conv emits `#include "lvgl/lvgl.h"` without
# it, and the library is on the include path as lvgl/ rather than its parent.
DEFINES  := -DLV_CONF_INCLUDE_SIMPLE -DLV_LVGL_H_INCLUDE_SIMPLE
WARN     := -Wall -Wno-unused-parameter -Wno-unused-function

# -MMD -MP emits a .d file per object listing the headers it included, so a
# header change rebuilds exactly what depends on it. Not a nicety: raising
# NEWS_SUMMARY_LEN changes sizeof(news_article_t), and without this the stubs
# recompiled while news_ui.o kept the old stride — the UI then read the article
# array at the wrong offsets and rendered convincing garbage rather than
# failing to build.
CFLAGS   := -g -O1 -MMD -MP $(WARN) $(INCLUDES) $(DEFINES)
CXXFLAGS := $(CFLAGS) -std=c++17
LDFLAGS  := -L$(SDL_PFX)/lib -lSDL2

LVGL_SRC := $(shell find $(LVGL)/src -name '*.c')
SIM_SRC  := $(abspath sim_main.c) $(abspath stubs/sim_stubs.c)
# The generated Montserrat builds (tools/gen_fonts.sh). Wildcarded rather than
# listed, so adding a size doesn't need an edit here as well as in lv_conf.h.
FONT_SRC := $(wildcard $(PROJ)/src/fonts/*.c)
UI_SRC   := $(PROJ)/src/news_ui.cpp

OBJDIR   := build
# Sources are absolute, so stripping the leading / keeps every object inside
# build/ regardless of where the source lives.
OBJS     := $(patsubst /%.c,$(OBJDIR)/%.o,$(LVGL_SRC) $(SIM_SRC) $(FONT_SRC)) \
            $(patsubst /%.cpp,$(OBJDIR)/%.o,$(UI_SRC))

sim: $(OBJS)
	$(CXX) $(OBJS) $(LDFLAGS) -o $@
	@echo "built ./sim"

# Header dependencies from the .d files above. Both this and the lv_conf rule
# must come after the sim rule: a target listed earlier becomes make's default
# goal, and $(OBJS) would win.
-include $(OBJS:.o=.d)

# lv_conf.h still gets an explicit edge. It decides which fonts and widgets
# compile at all, and it has to invalidate everything even on the first build,
# before any .d file exists — otherwise enabling a font leaves the stale empty
# object in place and the link fails with an undefined symbol.
LV_CONF := $(PROJ)/include/lv_conf.h
$(OBJS): $(LV_CONF)

$(OBJDIR)/%.o: /%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

$(OBJDIR)/%.o: /%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) -c $< -o $@

run: sim
	./sim

shot: sim
	./sim --shot shot.bmp

clean:
	rm -rf $(OBJDIR) sim shot.bmp

.PHONY: sim run shot clean
