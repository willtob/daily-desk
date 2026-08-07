//
//  main.swift — the floating panel that hosts the reader.
//
//  This is the desktop counterpart to the ESP32 display: same digest, same
//  backend, same two-view reader, but pinned to a corner of the Mac instead
//  of sitting on a 172x640 panel on the desk.
//
//  Three decisions worth knowing about:
//
//  * Accessory activation policy — no Dock icon, no app switcher entry. A
//    widget that steals a Dock slot is a small app, not a widget.
//  * A real NSPanel at .floating level, not a borderless NSWindow. It keeps
//    the digest above whatever you are working in, follows you across Spaces,
//    and does not disappear when the app deactivates.
//  * The app *does* activate on click. A .nonactivatingPanel would feel
//    lighter, but then the main menu is never the active menu and Cmd-Q
//    silently does nothing — with the traffic lights hidden, that leaves no
//    way to quit.
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

/// A borderless-looking panel that can still take key input.
///
/// `canBecomeKey` has to be overridden: a panel whose title bar is hidden is
/// treated as chrome-less, and without this the arrow keys and Escape never
/// reach SwiftUI.
final class NewsPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

// MARK: - Delegate

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var panel: NewsPanel!
    private let store = DigestStore(baseURL: resolveBaseURL())

    private static let defaultSize = NSSize(width: 340, height: 700)
    private static let minSize     = NSSize(width: 260, height: 320)
    private static let autosave    = "ESPNewsPanel"

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
        panel = NewsPanel(
            contentRect: NSRect(origin: .zero, size: Self.defaultSize),
            styleMask: [.titled, .closable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        // Chrome off: the panel's own header row is the title bar.
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true
        panel.isMovableByWindowBackground = true

        // Floating behaviour.
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]

        panel.backgroundColor = NSColor(Theme.bg)
        panel.minSize = Self.minSize
        panel.isReleasedWhenClosed = false

        panel.contentView = NSHostingView(rootView: RootView(store: store))

        // Restores position and size across launches; only the first run
        // needs placing, and the top-right corner is where a glanceable
        // panel belongs.
        panel.setFrameAutosaveName(Self.autosave)
        if panel.frame.size == .zero || !panel.setFrameUsingName(Self.autosave) {
            positionTopRight()
        }

        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
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
