//
//  AreaBadge.swift — the interest-area label, and the pull that rates a story.
//
//  Grab the coloured capsule and pull it. A blob of it stretches out after the
//  cursor and springs back when you let go, leaving a heart or a frown behind.
//  Down is a like, up is a dislike, and the same pull again takes it off again.
//  The shape and the physics are in SlimePull; this file is the badge, the
//  gesture, and what happens to a verdict the backend refuses.
//
//  ── The badge does not move ───────────────────────────────────────────────
//
//  Nothing here changes size or position while a pull is in progress, and that
//  is the correction that produced this version. The first one stretched the
//  badge's own pill with negative padding, which grew the badge, which pushed
//  the headline underneath it down the card — you pulled the label and the
//  story flinched. The blob is drawn in a `.background`, which never
//  participates in layout, and shapes are not clipped to their frame, so it can
//  hang forty points off a badge that is still exactly the rectangle it was.
//
//  The label and tint come from `AreaStyle` exactly as they did before this
//  file existed — one map, shared with the firmware's AREA_STYLES. This is the
//  same badge both the deck and the open story were already drawing inline,
//  lifted into one place so the gesture only had to be written once.
//
//  ── Why a SwiftUI DragGesture and not TrackpadPager ───────────────────────
//
//  The obvious precedent is wrong. TrackpadPager is an NSEvent monitor because
//  a two-finger swipe is *not a gesture* — it arrives as `scrollWheel` events
//  that SwiftUI on macOS 14 cannot see at all, so there was nothing to attach
//  to and no view to attach it to. None of that applies here. A press and drag
//  of the mouse is precisely what DragGesture is, and this pull has the one
//  requirement a global monitor is worst at: it must start *on this capsule*
//  and nowhere else.
//
//  Hit-testing it by hand would mean publishing the badge's frame up through a
//  preference key and testing `NSEvent.mouseLocation` against it — and that
//  frame is wrong the moment you look at it, because the card carrying this
//  badge is scaled, offset, tilted and rotated in 3D by `Slot`. SwiftUI already
//  hit-tests through all of that correctly. Reimplementing it against a
//  rotation3DEffect to avoid a gesture API that fits is the wrong trade.
//
//  ── The window drag ───────────────────────────────────────────────────────
//
//  The panel used to be draggable from anywhere in its body, which put a
//  press-and-vertical-drag on the root of the same view this one lives in. Two
//  gestures of the same shape, one inside the other.
//
//  That was first arbitrated: this gesture took a lower minimumDistance so it
//  always started first, and set a flag the window drag checked before moving
//  anything. It read well and it did not survive a real hand — dragging the
//  badge still moved the widget. Synthesised events had said otherwise, which
//  is worth remembering about synthesised events.
//
//  So the overlap is gone instead of refereed. The window drag now lives only
//  on the header strip (RootView) and, while a story is open, on the gap beside
//  the badge in its top row (DetailView). Nothing on a card moves the panel.
//  There is no precedence left to reason about, no flag to keep in sync, and
//  anything added to a card later inherits the fix for free. `minimumDistance`
//  here is now only about telling a pull from a click.
//
//  An upward pull does now *draw* across the header strip, which is the one
//  place the window drag still lives. That is not a return of the problem: the
//  blob is a background with hit testing off, the badge's gesture keeps the
//  pointer for the life of the drag wherever it travels, and the two regions
//  that can begin a gesture remain disjoint.
//

import AppKit
import SwiftUI

/// The badge as everything else asks for it.
///
/// Split in two because of how SwiftUI observation works, and the split is not
/// cosmetic. A store held as a plain `let` is a reference SwiftUI never
/// subscribes to, so the view redraws only when something *else* invalidates
/// it — which it did, and the badge sat there showing a heart on a story the
/// backend had recorded as a dislike. `@ObservedObject` cannot be optional, so
/// the observing half is its own view and this one decides whether there is
/// anything to observe.
struct AreaBadge: View {

    let article: Article

    /// Nil disables the gesture and the mark entirely: snapshot rendering, and
    /// the peek cards behind the top one, which are not interactive.
    var feedback: FeedbackStore?
    var client: NewsClient?

    var body: some View {
        let style = AreaStyle.forArticle(article)
        if let feedback, let client {
            PullableAreaBadge(article: article, feedback: feedback, client: client)
        } else {
            BadgeCapsule(style: style) { EmptyView() }
                .background(BadgePill(style: style))
        }
    }
}

/// The content of a badge: one definition of the type, the spacing and the
/// padding, so the inert badge and the pullable one cannot drift apart.
///
/// The pill behind it is deliberately *not* included. The pullable badge has to
/// stretch its pill independently of its content, which is the whole gesture —
/// so the two are applied separately and `BadgePill` is what keeps them
/// matching.
struct BadgeCapsule<Accessory: View>: View {

    let style: AreaStyle
    @ViewBuilder var accessory: Accessory

    static var hPad: CGFloat { 7 }
    static var vPad: CGFloat { 3 }

    var body: some View {
        HStack(spacing: 4) {
            Text(style.label)
                .font(.system(size: Theme.metaSize, weight: .bold))
                .tracking(Theme.metaTracking)
            accessory
        }
        .foregroundStyle(style.tint)
        .padding(.horizontal, Self.hPad)
        .padding(.vertical, Self.vPad)
    }
}

/// The dark lozenge a badge sits on.
///
/// The hairline is the reason this is a type rather than two lines inline. The
/// pill is near-black on a card that is bright, which works everywhere except
/// the one place the gesture goes: pulled *up*, the badge leaves the card and
/// crosses onto the shell, which is also near-black, and the thing being
/// dragged all but disappears exactly when it most needs to be legible.
///
/// A rim in the area's own tint fixes that without inventing a colour — against
/// the shell it is the only thing visible, and against the card it reads as a
/// faint edge on a badge that already had a hard boundary. Every badge carries
/// it, pullable or not, so a story does not change appearance depending on
/// whether it happens to be the top card.
struct BadgePill: View {

    let style: AreaStyle
    var flashing = false

    /// Enough to separate the pill from the shell, not enough to read as a
    /// border on the card. Tuned by eye against both backgrounds, and shared
    /// with the blob so the two carry one rim rather than two weights of one.
    static let rim = 0.45

    var body: some View {
        Capsule()
            .fill(flashing ? Theme.accent : Theme.ink)
            .overlay(
                Capsule().strokeBorder(style.tint.opacity(Self.rim), lineWidth: 1)
            )
    }
}

private struct PullableAreaBadge: View {

    let article: Article

    @ObservedObject var feedback: FeedbackStore
    let client: NewsClient

    /// The pull's offset and where it was grabbed. Owned by an object rather
    /// than by @State because the return home is integrated frame by frame —
    /// see SlimeMotion.
    @StateObject private var motion = SlimeMotion()

    /// The reference's latch: the verdict is recorded once, at the moment the
    /// threshold is crossed, and not again until the pointer is lifted.
    ///
    /// Committing on the crossing rather than on release is the thing that
    /// makes the gesture feel like breaking something off. It also means the
    /// heart is already there while the blob is still stretched, so the pull
    /// and its result are one event rather than two.
    @State private var popped = false

    /// Sideways wobble, used only to show a rejected verdict going away.
    @State private var shake: CGFloat = 0
    @State private var flashing = false

    private var style: AreaStyle { AreaStyle.forArticle(article) }

    /// Fixed room for the mark, so rating a story does not resize its badge.
    private static let markSlot: CGFloat = 10

    /// The mark arriving. Quick and only lightly springy: it lands while the
    /// blob is still on its way home, and anything looser reads as a second,
    /// competing animation.
    private static let markAnim = Animation.spring(response: 0.26, dampingFraction: 0.68)

    // MARK: - Body

    var body: some View {
        BadgeCapsule(style: style) {
            // A fixed slot, always present on a live badge, so the pill does
            // not change width as a verdict comes and goes.
            ZStack {
                if let current {
                    SlimeMark(mark: current.glyph, color: style.tint)
                        .transition(.scale(scale: 0.4).combined(with: .opacity))
                }
            }
            .frame(width: Self.markSlot, height: Theme.metaSize)
            .animation(Self.markAnim, value: current)
        }
        // Everything that moves is in here, and none of it is layout. The pill
        // is a background, the blob is drawn over the pill and under the label,
        // and both are free to paint outside a frame that never changes.
        .background {
            ZStack {
                BadgePill(style: style, flashing: flashing)
                SlimeBlob(offset: motion.offset, grabX: motion.grabX, tint: style.tint)
            }
            .allowsHitTesting(false)
        }
        .offset(x: shake)
        // Draw order among the badge's siblings in its row. Getting above the
        // headline below it is the row's job, not this one — see CardFace.
        .zIndex(1)
        .contentShape(Capsule())
        .gesture(pullGesture)
        .help(helpText)
        .onChange(of: rejectionID) { flashRejection() }
        // Paging swaps the article under a badge that may still be mid-spring.
        .onChange(of: article.id) {
            motion.cancel()
            shake = 0
            popped = false
        }
    }

    // MARK: - Gesture

    private var pullGesture: some Gesture {
        DragGesture(minimumDistance: SlimePull.minimumDistance)
            .onChanged { value in
                // startLocation is in the badge's own coordinates, which is the
                // same space the shapes are handed — so the anchor lands under
                // the pointer without publishing a frame anywhere.
                motion.drag(to: value.translation, grabX: value.startLocation.x)
                commitIfCrossed()
            }
            .onEnded { _ in
                motion.release()
                popped = false
            }
    }

    /// Record the verdict the instant the pull earns it, once per drag.
    private func commitIfCrossed() {
        guard !popped, SlimePull.commits(motion.offset) else { return }
        popped = true

        // A tick at the break, so the threshold can be felt rather than
        // guessed at. Trackpads without a haptic engine ignore it.
        NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now)

        // Direction only. Whether that records a verdict or takes the existing
        // one back off is the store's decision, so the toggle rule lives in
        // exactly one place.
        let direction: FeedbackStore.Verdict = motion.offset.height >= 0 ? .like : .dislike
        Task { await feedback.apply(direction, to: article.url, using: client) }
    }

    // MARK: - State

    private var current: FeedbackStore.Verdict? {
        feedback.verdict(for: article.url)
    }

    private var rejectionID: UUID? {
        guard let rejection = feedback.rejection, rejection.url == article.url else {
            return nil
        }
        return rejection.id
    }

    private var helpText: String {
        switch current {
        case .like:    return "Liked — pull down again to undo"
        case .dislike: return "Disliked — pull up again to undo"
        case nil:      return "Pull down to like, up to dislike"
        }
    }

    // MARK: - Rejection

    /// The backend refused a verdict the panel had already drawn.
    ///
    /// The store has put the mark back by the time this runs; this is only so
    /// that the mark going away is *seen*. A silent revert on a small panel is
    /// indistinguishable from the gesture never having registered, which would
    /// send anyone straight into pulling it again.
    private func flashRejection() {
        guard rejectionID != nil else { return }
        flashing = true
        withAnimation(.spring(response: 0.12, dampingFraction: 0.3)) { shake = 7 }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
            // Low damping on the way back turns one displacement into a wobble,
            // which is a shake without keyframing one.
            withAnimation(.spring(response: 0.38, dampingFraction: 0.28)) { shake = 0 }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.55) {
            withAnimation(.easeOut(duration: 0.25)) { flashing = false }
        }
    }
}

/// Which glyph stands for a verdict. Here rather than on FeedbackStore, which
/// has no business knowing what a verdict looks like.
extension FeedbackStore.Verdict {
    var glyph: SlimeGlyph.Mark {
        switch self {
        case .like:    return .like
        case .dislike: return .dislike
        }
    }
}
