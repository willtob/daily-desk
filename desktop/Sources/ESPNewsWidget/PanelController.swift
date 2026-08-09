//
//  PanelController.swift — where the panel sits in the window stack.
//
//  Two placements, because they are genuinely different tools:
//
//  DESKTOP   The widget behaviour. The panel lives at the wallpaper layer,
//            behind every ordinary window, and never comes forward. It is
//            part of the desk rather than part of what you are doing — you
//            see it when the desktop is visible and it stays out of the way
//            otherwise. This is what "sits on my desktop" means.
//
//  FLOATING  Always on top of everything, across Spaces. Useful while
//            actively reading, intrusive the rest of the time.
//
//  The macOS window level constants are the whole trick. kCGDesktopIconWindow
//  is the layer Finder draws desktop icons on: above the wallpaper, below all
//  application windows. Übersicht and GeekTool sit here too. One level lower
//  (kCGDesktopWindow) would put the panel *under* the wallpaper on some
//  configurations, where it is invisible and very confusing to debug.
//

import AppKit
import SwiftUI

@MainActor
final class PanelController: ObservableObject {

    enum Placement: String, CaseIterable {
        case desktop
        case floating

        var title: String {
            switch self {
            case .desktop:  return "Sit on desktop"
            case .floating: return "Float on top"
            }
        }
    }

    @Published var placement: Placement {
        didSet {
            UserDefaults.standard.set(placement.rawValue, forKey: Self.key)
            apply()
        }
    }

    /// Unowned by design: the delegate owns the panel, this only steers it.
    weak var panel: NSPanel?

    private static let key = "placement"

    init() {
        let stored = UserDefaults.standard.string(forKey: Self.key)
        // Desktop is the default. The point of the thing is to sit on the
        // desktop; a reader that covers your work until you tell it not to
        // is the wrong first impression.
        self.placement = stored.flatMap(Placement.init(rawValue:)) ?? .desktop
    }

    func apply() {
        guard let panel else { return }

        // isFloatingPanel must be set *before* the level, never after: it is
        // a level setter wearing a Bool's clothing, and assigning it silently
        // rewrites the window level (true -> .floating, false -> .normal).
        // Setting it afterwards drops the panel straight back to layer 0,
        // which looks exactly like the desktop level having been ignored.
        panel.isFloatingPanel = (placement == .floating)

        switch placement {
        case .desktop:
            // One level *above* the icons, not level with them.
            //
            // Finder draws desktop icons in a full-screen window at exactly
            // kCGDesktopIconWindow. Sitting at the same level loses the tie
            // twice over: the icons paint on top of the widget, and Finder's
            // window — being full-screen — swallows every click in the
            // region before it can reach us. The widget looks present and is
            // completely inert, and no amount of fixing event handling on
            // this side helps, because no event ever arrives.
            //
            // +1 is still astronomically below kCGNormalWindowLevel (0), so
            // every ordinary window stays in front and this is still "on the
            // desktop". It just stops being underneath the desktop.
            panel.level = NSWindow.Level(
                rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) + 1
            )
            // .stationary keeps it pinned while Spaces slide past, which is
            // what makes it read as part of the desktop rather than as a
            // window that follows you. .ignoresCycle keeps it out of Cmd-Tab
            // and Exposé, where a widget has no business appearing.
            panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
            // A window shadow is the single strongest "this is a window" cue.
            panel.hasShadow = false

        case .floating:
            panel.level = .floating
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            panel.hasShadow = true
        }
    }

    func toggle() {
        placement = (placement == .desktop) ? .floating : .desktop
    }

    // MARK: - Dragging
    //
    // Done by hand because AppKit's isMovableByWindowBackground cannot be
    // used here: it makes every mouse-down in the widget a window drag and
    // kills all interaction (see ClickThroughHostingView).
    //
    // The positions come from NSEvent.mouseLocation, in screen coordinates,
    // rather than from the gesture's own translation. A SwiftUI drag reports
    // movement relative to the window, and this gesture *moves the window* —
    // so feeding translation back in makes the widget chase the cursor and
    // skitter across the screen. Screen coordinates do not move under us.

    private var dragAnchor: (window: NSPoint, mouse: NSPoint)?

    func dragChanged() {
        guard let panel else { return }

        let mouse = NSEvent.mouseLocation
        let anchor = dragAnchor ?? (window: panel.frame.origin, mouse: mouse)
        dragAnchor = anchor

        panel.setFrameOrigin(NSPoint(
            x: anchor.window.x + (mouse.x - anchor.mouse.x),
            y: anchor.window.y + (mouse.y - anchor.mouse.y)
        ))
    }

    func dragEnded() {
        dragAnchor = nil
        guard let panel else { return }

        snapToEdges(panel)

        // setFrameOrigin does not trigger the autosave that a user-driven
        // move would, so the position has to be written explicitly or it is
        // forgotten on the next launch.
        guard !panel.frameAutosaveName.isEmpty else { return }
        panel.saveFrame(usingName: panel.frameAutosaveName)
    }

    // MARK: - Snapping
    //
    // Desktop widgets line up. Dropped by hand a panel lands three or four
    // points off whatever is next to it, which is exactly far enough to look
    // like a mistake and not far enough to be worth nudging.
    //
    // The snap is to `visibleFrame`, not `frame`: that excludes the menu bar
    // and the Dock, so "top right" means below the menu bar rather than under
    // it, and a panel snapped to the bottom of a screen with a Dock does not
    // end up behind it.

    /// How close an edge has to be before it grabs, and the gap it leaves.
    private static let snapDistance: CGFloat = 18
    private static let snapMargin:   CGFloat = 20

    private func snapToEdges(_ panel: NSPanel) {
        // The screen the panel is mostly on, not the one with the mouse — the
        // cursor may already have left it by the time the drag ends.
        let screens = NSScreen.screens
        guard let screen = screens.max(by: { a, b in
            overlap(panel.frame, a.visibleFrame) < overlap(panel.frame, b.visibleFrame)
        }) ?? NSScreen.main else { return }

        let area = screen.visibleFrame
        var origin = panel.frame.origin
        let size = panel.frame.size

        let left   = area.minX + Self.snapMargin
        let right  = area.maxX - size.width  - Self.snapMargin
        let bottom = area.minY + Self.snapMargin
        let top    = area.maxY - size.height - Self.snapMargin

        if abs(origin.x - left)  < Self.snapDistance { origin.x = left }
        if abs(origin.x - right) < Self.snapDistance { origin.x = right }
        if abs(origin.y - bottom) < Self.snapDistance { origin.y = bottom }
        if abs(origin.y - top)    < Self.snapDistance { origin.y = top }

        guard origin != panel.frame.origin else { return }
        panel.setFrameOrigin(origin)
    }

    private func overlap(_ a: NSRect, _ b: NSRect) -> CGFloat {
        let r = a.intersection(b)
        return r.isNull ? 0 : r.width * r.height
    }
}
