//
//  TrackpadPager.swift — two fingers sideways flips the deck.
//
//  A two-finger trackpad swipe is not a gesture in the AppKit sense; it is a
//  stream of `scrollWheel` events with precise deltas and a phase. SwiftUI on
//  macOS 14 has no way to see those, so this drops to a local event monitor,
//  which sees events before they are dispatched to any view.
//
//  A monitor rather than an NSView subclass on purpose. The article body is an
//  NSScrollView, and a view-based catcher would have to win a fight with it
//  over every event — either sitting in front, where it swallows the vertical
//  scrolling that makes long stories readable, or behind, where it only sees
//  what the scroll view declined to take. The monitor sees everything first and
//  decides on the one thing that actually distinguishes the two: which axis
//  the finger moved along.
//
//  ── One swipe is many events ──────────────────────────────────────────────
//
//  This is the part that goes wrong if you treat scroll deltas naively. A
//  single flick produces dozens of `.changed` events and then a *second*
//  stream of momentum events as the scroll coasts to a stop. Summing deltas
//  and paging whenever the total crosses a threshold turns one swipe into ten
//  cards. So the pager latches: it fires once, then ignores everything until
//  the gesture genuinely ends, and swallows the momentum tail outright.
//
//  ── Direction ─────────────────────────────────────────────────────────────
//
//  `isDirectionInvertedFromDevice` is true when "natural scrolling" is on,
//  which flips the sign of the delta. Normalising by it means the *finger*
//  direction is what counts, so a swipe left always means the next story
//  whichever way the preference is set — the same physical motion as flicking
//  the top card off a pile.
//

import AppKit

@MainActor
final class TrackpadPager: ObservableObject {

    /// Called with +1 for the next story, -1 for the previous.
    var onPage: ((Int) -> Void)?

    /// Set true to log what the monitor actually receives. Kept because the
    /// interesting question about this feature is not the arithmetic, it is
    /// whether a panel at the desktop window level is sent scroll events at
    /// all — and if it is not, nothing else here has a symptom worth reading.
    var trace = false

    /// How far the fingers must travel before it counts. Trackpad deltas are
    /// small and numerous; a wheel arrives in a few large lumps, so it gets a
    /// lower bar or a single detent would never reach the threshold.
    private static let trackpadThreshold: CGFloat = 45
    private static let wheelThreshold: CGFloat = 6

    /// How much more horizontal than vertical the movement has to be. Well
    /// above 1: a swipe meant as horizontal still carries some vertical drift,
    /// but a *scroll* meant to read an article must never page the deck.
    private static let axisRatio: CGFloat = 1.6

    private var monitor: Any?
    private var travelled: CGFloat = 0
    private var fired = false

    func start() {
        guard monitor == nil else { return }
        monitor = NSEvent.addLocalMonitorForEvents(matching: .scrollWheel) { [weak self] event in
            guard let self else { return event }
            return self.handle(event)
        }
    }

    func stop() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
    }

    deinit {
        // deinit is nonisolated; the monitor token is safe to remove anywhere.
        if let monitor { NSEvent.removeMonitor(monitor) }
    }

    private func handle(_ event: NSEvent) -> NSEvent? {
        let dx = event.scrollingDeltaX
        let dy = event.scrollingDeltaY

        if trace {
            NSLog("[pager] dx=%.1f dy=%.1f phase=%lu momentum=%lu precise=%d",
                  dx, dy, event.phase.rawValue, event.momentumPhase.rawValue,
                  event.hasPreciseScrollingDeltas ? 1 : 0)
        }

        // The momentum tail belongs to a swipe that has already been counted.
        // Swallowed rather than passed on, so a flick that paged the deck does
        // not then coast the article underneath it.
        if !event.momentumPhase.isEmpty { return fired ? nil : event }

        if event.phase.contains(.began) {
            travelled = 0
            fired = false
        }

        // Vertical wins ties and anything close to one. Reading is the more
        // common intent and the more annoying thing to break.
        guard abs(dx) > abs(dy) * Self.axisRatio else {
            if event.phase.contains(.ended) || event.phase.contains(.cancelled) {
                travelled = 0
                fired = false
            }
            return event
        }

        travelled += dx

        let threshold = event.hasPreciseScrollingDeltas
            ? Self.trackpadThreshold
            : Self.wheelThreshold

        if !fired, abs(travelled) >= threshold {
            fired = true
            // Natural scrolling flips the sign; normalise so the finger's
            // direction is what decides. Swipe left, next story.
            let finger = event.isDirectionInvertedFromDevice ? travelled : -travelled
            onPage?(finger < 0 ? 1 : -1)
        }

        if event.phase.contains(.ended) || event.phase.contains(.cancelled) {
            travelled = 0
            fired = false
        }

        // Horizontal is ours either way, fired or still accumulating. Letting
        // it through would scroll the article sideways under the swipe.
        return nil
    }
}
