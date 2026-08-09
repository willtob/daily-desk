//
//  LoginItem.swift — start with the machine, like a widget does.
//
//  A desktop widget you have to remember to launch is not really a widget; it
//  is an app you keep forgetting about. SMAppService is the modern way to
//  register one, and unlike the old LaunchAgent plist it needs no file in
//  ~/Library/LaunchAgents and no helper target — the bundle registers itself.
//
//  Two things worth knowing before debugging this:
//
//  * **It only works from a bundle.** `swift run` produces a bare executable
//    with no Info.plist, and SMAppService has nothing to register — it throws
//    "Unable to read plist". So `make dev` will always report unavailable and
//    that is correct, not a bug. Use `make run` or `make install`.
//
//  * **The user can override it in System Settings**, under General → Login
//    Items, and that override wins. So the menu has to read `status` back
//    rather than trusting whatever it last set, or the checkmark drifts out of
//    step with reality the first time someone turns it off there.
//

import Foundation
import ServiceManagement

enum LoginItem {

    /// Whether registration is possible at all — false for an unbundled build.
    static var available: Bool {
        Bundle.main.bundleIdentifier != nil && Bundle.main.bundleURL.pathExtension == "app"
    }

    /// Read from the system every time, never cached. See the note above.
    static var isEnabled: Bool {
        guard available else { return false }
        return SMAppService.mainApp.status == .enabled
    }

    /// Returns the state actually achieved, which is not always the one asked
    /// for: registration can fail, and silently reporting success would leave
    /// a checked menu item that does nothing.
    @discardableResult
    static func set(_ on: Bool) -> Bool {
        guard available else { return false }
        do {
            if on {
                // Re-registering an already-registered app throws rather than
                // being a no-op, so the state is checked first.
                if SMAppService.mainApp.status != .enabled {
                    try SMAppService.mainApp.register()
                }
            } else {
                try SMAppService.mainApp.unregister()
            }
        } catch {
            NSLog("login item \(on ? "register" : "unregister") failed: \(error)")
        }
        return isEnabled
    }
}
