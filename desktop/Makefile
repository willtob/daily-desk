# ESP News desktop panel.
#
#   make          build ESP News.app into build/
#   make run      build, then launch it (relaunching over a running copy)
#   make dev      swift run — faster loop, but no bundle, so the Dock icon
#                 shows and the Info.plist exemptions do not apply
#   make install  copy the bundle to /Applications
#   make reload   install, then relaunch the /Applications copy — the one
#                 command that is always right. `run` launches build/, but
#                 if /Applications also has a copy (it usually does — that's
#                 the one Spotlight and the Dock icon point at) the two share
#                 a bundle identifier and LaunchServices can resolve a
#                 double-click or `open` back to the stale one regardless of
#                 which was just rebuilt. `reload` sidesteps the ambiguity by
#                 only ever touching the /Applications copy.
#   make clean
#
# There is no Xcode project here on purpose: Command Line Tools plus Swift
# Package Manager builds this, and assembling the .app by hand is a dozen
# lines. A WidgetKit widget would be the thing that forces a full Xcode
# install — see README.md.

APP_NAME  := ESP News
BIN       := ESPNewsWidget
BUILD_DIR := build
APP       := $(BUILD_DIR)/$(APP_NAME).app
CONFIG    := release

.PHONY: all run dev install reload clean

all: $(APP)

$(APP): $(shell find Sources -name '*.swift') Resources/Info.plist Package.swift
	swift build -c $(CONFIG)
	rm -rf "$(APP)"
	mkdir -p "$(APP)/Contents/MacOS" "$(APP)/Contents/Resources"
	cp Resources/Info.plist "$(APP)/Contents/Info.plist"
	cp "$$(swift build -c $(CONFIG) --show-bin-path)/$(BIN)" "$(APP)/Contents/MacOS/$(BIN)"
	# Ad-hoc signature. Unsigned bundles still run, but macOS re-prompts for
	# network access on every launch and the panel loses its saved position.
	codesign --force --sign - "$(APP)"
	@echo "built $(APP)"

run: $(APP)
	# The pause is not superstition: LaunchServices returns -600 if the old
	# process is still going away when the new `open` lands.
	-pkill -x $(BIN) 2>/dev/null; sleep 1
	open "$(APP)"

dev:
	swift run

install: $(APP)
	rm -rf "/Applications/$(APP_NAME).app"
	cp -R "$(APP)" /Applications/
	@echo "installed /Applications/$(APP_NAME).app"

reload: install
	-pkill -x $(BIN) 2>/dev/null; sleep 1
	open "/Applications/$(APP_NAME).app"

clean:
	swift package clean
	rm -rf $(BUILD_DIR) .build
