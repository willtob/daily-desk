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
#
# The /Applications copy is supervised by a LaunchAgent now
# (~/Library/LaunchAgents/com.willtobin.esp-news-widget.plist), which
# restarts it within a few seconds no matter how it exits — crash, kill, or a
# clean Quit — not just at login, the way a plain login item does. The widget
# is meant to be permanently on the desktop, so quitting from the UI only
# ever buys ~5 seconds; actually stopping it means unloading the job:
# `launchctl bootout gui/$$(id -u)/com.willtobin.esp-news-widget`.
# `reload` asks launchd to do its own
# kill-and-relaunch (`kickstart -k`) rather than pkill+open, so the two never
# race and double-launch. `run` and `dev` build a *different* copy (build/,
# or no bundle at all) but still share the ESPNewsWidget process name, so a
# pkill from either one also takes down the supervised copy — which is fine,
# it comes back on its own within ThrottleInterval. To test without that
# happening mid-session: `launchctl bootout gui/$$(id -u)/com.willtobin.esp-news-widget`
# first, then `launchctl bootstrap gui/$$(id -u) ~/Library/LaunchAgents/com.willtobin.esp-news-widget.plist`
# when done.

APP_NAME  := ESP News
BIN       := ESPNewsWidget
BUILD_DIR := build
APP       := $(BUILD_DIR)/$(APP_NAME).app
CONFIG    := release
WIDGET_JOB := com.willtobin.esp-news-widget

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
	# launchd owns this process now — asking it to kill-and-relaunch its own
	# job, instead of pkill+open, is what keeps this from racing the
	# watchdog's own KeepAlive restart into a second copy. See the note above.
	launchctl kickstart -k gui/$(shell id -u)/$(WIDGET_JOB)

clean:
	swift package clean
	rm -rf $(BUILD_DIR) .build
