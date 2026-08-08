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
            panel.level = NSWindow.Level(
                rawValue: Int(CGWindowLevelForKey(.desktopIconWindow))
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
}
