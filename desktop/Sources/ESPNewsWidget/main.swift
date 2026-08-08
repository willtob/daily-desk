//
//  main.swift — the window the widget lives in.
//
//  The desktop counterpart to the ESP32 display: same digest, same backend,
//  same scores, but a 288x300 card deck on the wallpaper instead of a 172x640
//  panel on the desk.
//
//  Three decisions worth knowing about:
//
//  * Accessory activation policy — no Dock icon, no app switcher entry. A
//    widget that steals a Dock slot is a small app, not a widget.
//  * An NSPanel rather than a plain NSWindow, so it can sit across Spaces and
//    not vanish when the app deactivates. Where it sits in the window stack
//    is PanelController's business, not this file's.
//  * The app *does* activate on click. A .nonactivatingPanel would feel
//    lighter, but then the main menu is never the active menu and Cmd-Q
//    silently does nothing — with no chrome at all, that leaves no way out.
//

import AppKit
import SwiftUI

// MARK: - Configuration

/// Where the FastAPI backend lives.
///
/// Defaults to loopback on 8010 rather than 8000: port 8000 is taken by
/// Docker on this machine, which is why esp-serve is run with --port 8010.
/// Override with ESP_NEWS_BASE_URL, or persistently with
/// `defaults write com.willtobin.esp-news-widget baseURL http://…`.
private func resolveBaseURL() -> URL {
    let fallback = URL(string: "http://127.0.0.1:8010")!

    if let env = ProcessInfo.processInfo.environment["ESP_NEWS_BASE_URL"],
       let url = URL(string: env) {
        return url
    }
    if let stored = UserDefaults.standard.string(forKey: "baseURL"),
       let url = URL(string: stored) {
        return url
    }
    return fallback
}

// MARK: - Panel

/// A borderless panel that can still take key input.
///
/// `canBecomeKey` has to be overridden: AppKit will not make a chrome-less
/// window key on its own, and without this the arrow keys and Escape never
/// reach SwiftUI. It still only applies in floating placement — a window at
/// the wallpaper layer never becomes key at all, which is why the deck has
/// on-screen buttons.
final class NewsPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

/// A hosting view that lets clicks through to SwiftUI.
///
/// Two separate things conspire to eat every click in a widget like this, and
/// both have to be undone. They look identical from the outside — the thing
/// drags around the desktop perfectly and not one button inside it works —
/// which is why fixing only the first one appears to do nothing at all.
///
/// **First mouse.** An accessory app at the wallpaper layer is never the
/// active application, so every click on it is a "first mouse" click, and
/// AppKit's default is to swallow that click to activate the app rather than
/// deliver it to the view.
///
/// **Window dragging.** `NSHostingView.mouseDownCanMoveWindow` is `true`, and
/// a hit test anywhere in the content returns the hosting view itself — there
/// are no per-control subviews, because SwiftUI routes events internally. So
/// with `isMovableByWindowBackground` set, AppKit takes *every* mouse-down in
/// the entire widget as the start of a window drag and consumes it before
/// SwiftUI sees anything. Overriding this to `false` is what makes the deck
/// clickable; dragging is reimplemented as a gesture in PanelController.
final class ClickThroughHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override var mouseDownCanMoveWindow: Bool { false }

    required init(rootView: Content) { super.init(rootView: rootView) }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }
}

// MARK: - Delegate

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var panel: NewsPanel!
    private let store = DigestStore(baseURL: resolveBaseURL())
    private let controller = PanelController()

    // One card plus its stack, and nothing else. The list this replaced
    // needed 700 px to be worth reading; a deck needs the height of the card
    // on top, which is what makes it small enough to leave on the desktop.
    private static let defaultSize = NSSize(width: 288, height: 300)
    private static let minSize     = NSSize(width: 252, height: 250)
    // Bumped when the deck replaced the list: the saved frame from the tall
    // list panel would otherwise restore a 700 px window around a 300 px UI.
    private static let autosave    = "ESPNewsDeck"

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        buildMenu()
        buildPanel()
    }

    // Quitting is the only way out — there is no Dock icon to click to get
    // the panel back, so closing it and leaving the process running would
    // strand the app.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // MARK: Panel

    private func buildPanel() {
        // Borderless, not a titled window with its chrome hidden.
        //
        // Hiding the title bar is not the same as not having one. With
        // .fullSizeContentView the content view does get the whole frame
        // either way — that part is fine — but a .titled window still reports
        // `safeAreaInsets.top == 32` to whatever is inside it, hidden title
        // bar or not, and SwiftUI dutifully lays out below it. Borderless
        // reports 0. (Both measured; neither is documented.)
        //
        // With an opaque window background the inset is invisible: the widget
        // just starts a little lower than its frame. Against a transparent
        // window it is a bite out of the top. .ignoresSafeArea() is not the
        // fix — it pushes the header up into the strip AppKit still treats as
        // title bar, where it is simply not drawn. Nothing here wants a title
        // bar at all, so the honest fix is not to ask for one.
        panel = NewsPanel(
            contentRect: NSRect(origin: .zero, size: Self.defaultSize),
            styleMask: [.borderless, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        // Deliberately off. It is the obvious way to make a chrome-less
        // window draggable and it silently costs you every click in the
        // widget — see ClickThroughHostingView. RootView drags it instead.
        panel.isMovableByWindowBackground = false

        panel.hidesOnDeactivate = false
        panel.minSize = Self.minSize
        panel.isReleasedWhenClosed = false

        // The window draws nothing; RootView paints its own rounded shell.
        // AppKit's window corner radius is the small one it uses for
        // documents, and on a desktop already showing Calendar and Weather at
        // a much rounder radius, that difference is most of what separates
        // "widget" from "a window someone left open".
        panel.isOpaque = false
        panel.backgroundColor = .clear

        // Level and collection behaviour come from the placement — see
        // PanelController. Desktop is the default.
        controller.panel = panel
        controller.apply()

        panel.contentView = ClickThroughHostingView(
            rootView: RootView(store: store, controller: controller)
        )
        // Hover highlighting needs these even when the app is not active.
        panel.acceptsMouseMovedEvents = true

        // Restores position and size across launches; only the first run
        // needs placing, and the top-right corner is where a glanceable
        // panel belongs.
        panel.setFrameAutosaveName(Self.autosave)
        if panel.frame.size == .zero || !panel.setFrameUsingName(Self.autosave) {
            positionTopRight()
        }

        // A desktop widget appearing must not steal focus from whatever you
        // are typing in — orderFront, not makeKeyAndOrderFront. Floating mode
        // is an explicit "show me this now", so it may take focus.
        if controller.placement == .floating {
            panel.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        } else {
            panel.orderFront(nil)
        }
    }

    private func positionTopRight() {
        guard let screen = NSScreen.main else { return }
        let visible = screen.visibleFrame
        let margin: CGFloat = 20
        let origin = NSPoint(
            x: visible.maxX - Self.defaultSize.width - margin,
            y: visible.maxY - Self.defaultSize.height - margin
        )
        panel.setFrame(NSRect(origin: origin, size: Self.defaultSize), display: true)
    }

    // MARK: Menu
    //
    // An accessory app still needs a main menu: it is what makes Cmd-Q and
    // Cmd-R work at all. Nothing here is ever seen, since there is no menu
    // bar entry for a .accessory app.

    private func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Refresh Digest",
                        action: #selector(refresh),
                        keyEquivalent: "r")
        appMenu.addItem(withTitle: "Reload From Backend",
                        action: #selector(reload),
                        keyEquivalent: "l")
        appMenu.addItem(withTitle: "Toggle Desktop / Floating",
                        action: #selector(togglePlacement),
                        keyEquivalent: "d")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit ESP News",
                        action: #selector(NSApplication.terminate(_:)),
                        keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        NSApp.mainMenu = main
        // The items target this delegate rather than the responder chain, so
        // they work whether or not the panel is key.
        appMenu.items.forEach { $0.target = ($0.action == #selector(NSApplication.terminate(_:))) ? nil : self }
    }

    @objc private func refresh() { Task { await store.rebuild() } }
    @objc private func reload()  { Task { await store.load() } }
    @objc private func togglePlacement() { controller.toggle() }
}

// MARK: - Entry point

// Top-level code is not implicitly main-actor isolated here, but it does run
// on the main thread — which is exactly what assumeIsolated asserts.
MainActor.assumeIsolated {
    let args = CommandLine.arguments

    // --snapshot <dir> [--offline] renders the views to PNG and exits without
    // ever showing a window. See Snapshot.swift.
    if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
        let ok = Snapshot.run(directory: args[i + 1],
                              offline: args.contains("--offline"),
                              baseURL: resolveBaseURL())
        exit(ok ? 0 : 1)
    }

    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.run()
}
