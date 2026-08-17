//
//  RecorderPill.swift — the recorder, as one object.
//
//  A dark rounded pill with a circular button on the left and a recessed panel
//  on the right holding a scrolling waveform, a vertical playhead down its
//  centre and a small triangle at the playhead's foot. It is the reference the
//  Learn tab's explain screen was designed against, rebuilt in this app's
//  palette: the pill body is Theme.surface, the recess is Theme.bg so it reads
//  as cut into the pill rather than drawn on it, and the playhead takes the
//  accent that the reference spends on red.
//
//  **This is the whole of the speaking screen, and that is the point.** The
//  brief for the Learn tab's redesign was that explaining a topic out loud
//  should feel like talking about it, not like filling in a form by voice, so
//  there is no live transcript anywhere on this control. The waveform is the
//  feedback that it is hearing you. Watching your own words appear is exactly
//  what makes people proofread instead of think, and the transcript is only an
//  implementation detail of getting graded.
//
//  **The width is whatever the panel gives it, and that was learned the hard
//  way.** This was first built at a fixed 264 pt, derived from the 288 pt panel
//  Snapshot renders at. But the panel is resizable and the saved frame on the
//  machine it was built for is 261 wide, so the pill came out 27 pt wider than
//  its container — and because a fixed-width child widens the VStack around it,
//  that did not merely clip the pill. It pushed the *topic name* off the right
//  edge as well, and the only way to see the screen at all was to drag the
//  widget bigger. A control whose entire job is to be pressed must never be the
//  thing deciding how large the window has to be.
//
//  So the only fixed numbers here are vertical. The bar count is derived from
//  whatever width the recess actually gets:
//
//      bars = floor((recessWidth − 2×barInset + barGap) ÷ 4 pt pitch)
//
//  forced odd, so exactly one bar sits under the playhead — an even count
//  leaves the head in a gap, and at this size that is visible.
//
//  Centring the bar block instead of left-aligning it puts the playhead on the
//  recess's centre line at *every* width, which is worth the one line it costs.
//  With `span = n×pitch − barGap` and n odd:
//
//      headX = (w − span)/2 + (n/2)×pitch + barWidth/2
//            = w/2 − 2n + 1 + 2n − 1
//            = w/2                              for every w and every odd n
//
//  which is why the playhead below is simply centred rather than placed at a
//  computed offset that would have to be kept in sync with the bars.
//
//  **Which bars are bright.** The reference is a *playback* scrubber: left of
//  the head is played, right of it is not. A live recorder has no future, so
//  the mapping here is a tape head instead. Sound arrives at the right-hand
//  edge, travels left, and passes under the head. Everything left of the head
//  has been recorded — Theme.white. The ~1.8 s to its right has arrived but not
//  yet passed the head — Theme.dim, knocked back. Both sides are real input, so
//  the pill has the reference's two-tone silhouette without either half being
//  decoration.
//

import SwiftUI

struct RecorderPill: View {

    /// Newest last, each 0...1. Fewer than `barCount` entries is normal at the
    /// start of a recording and draws as a wave growing in from the right.
    let levels: [Double]

    /// Drives the glyph and the button's fill. Nothing else on this view knows
    /// about recording — the waveform draws whatever it is handed.
    let recording: Bool

    /// Greyed and unpressable while the analyzer is reserving a locale or
    /// downloading a model. Rare, but the first ever press can sit here.
    let preparing: Bool

    let action: () -> Void

    // MARK: - Geometry
    //
    // Internal rather than private so the offscreen measuring harness can
    // assert against the same numbers the view draws with, instead of against
    // a second copy of them that can drift.

    // Vertical only, and trimmed from the first pass's 66/46/10. The badge and
    // the two-line hint that used to sit above and below this control are gone,
    // so the pill no longer has to carry the screen on its own and can be the
    // size it needs rather than the size that filled the space it was given.
    static let height:     CGFloat = 56
    static let padding:    CGFloat = 9
    static let buttonSize: CGFloat = 38
    static let gap:        CGFloat = 8

    static var recessHeight: CGFloat { height - 2 * padding }

    /// Inset from the recess's edge to the first bar. Falls out of the bar
    /// arithmetic in the header rather than being chosen: it is whatever is
    /// left once a whole number of bars has been fitted.
    static let barInset:  CGFloat = 6
    static let barWidth:  CGFloat = 2
    static let barGap:    CGFloat = 2
    static var barPitch:  CGFloat { barWidth + barGap }

    /// How many bars fit a recess this wide, forced odd so exactly one sits
    /// under the playhead. `+ barGap` because n bars have only n−1 gaps
    /// between them; without it the last bar is dropped at exactly the widths
    /// where one more would have fitted.
    static func barCount(recessWidth: CGFloat) -> Int {
        let n = Int(((recessWidth - 2 * barInset + barGap) / barPitch).rounded(.down))
        guard n > 0 else { return 0 }
        return n.isMultiple(of: 2) ? n - 1 : n
    }

    /// A representative count, for poses and for anything that needs to make
    /// up level data before a real width exists. Not used for layout.
    static let nominalBarCount = barCount(recessWidth: 264 - 2 * padding - buttonSize - gap)

    /// A bar at rest. Not zero: a recorder showing nothing at all reads as
    /// broken, and the reference's idle bars are visible too.
    static let barMin: CGFloat = 3

    /// Vertical breathing room inside the recess, so the tallest bar does not
    /// touch the top and bottom edges.
    static let barPad: CGFloat = 6
    static var barMax: CGFloat { recessHeight - 2 * barPad }

    var body: some View {
        HStack(spacing: Self.gap) {
            button
            recess
        }
        .padding(Self.padding)
        // Height fixed, width taken from the container. See the header: a
        // fixed width here widens the whole VStack and pushes the topic name
        // off the panel, which is not a clipping bug but a layout one.
        .frame(maxWidth: .infinity)
        .frame(height: Self.height)
        .background(
            RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                .fill(Theme.surface)
        )
    }

    // MARK: - The button
    //
    // Filled accent at rest and recessed while running, which is the reverse of
    // the reference's flat dark circle and is deliberate. On the speaking
    // screen this is the *only* control — there is no Submit yet — so at rest
    // it has to read as the primary action the way `primaryButton` does. Once
    // it is running the loud thing on screen should be the waveform, so the
    // button sinks into the pill and keeps only its glyph.

    private var button: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(recording ? Theme.bg : Theme.accent)
                    .overlay(
                        Circle().stroke(Theme.rule, lineWidth: recording ? 1 : 0)
                    )

                if preparing {
                    // Three dots rather than a ProgressView, for the reason
                    // LearnView's grading screen uses a plain circle: an AppKit
                    // spinner renders as a "not allowed" badge through
                    // ImageRenderer and would blank this control in every
                    // snapshot.
                    HStack(spacing: 3) {
                        ForEach(0..<3, id: \.self) { _ in
                            Circle().fill(Theme.bg.opacity(0.55)).frame(width: 4, height: 4)
                        }
                    }
                } else {
                    Image(systemName: recording ? "pause.fill" : "mic.fill")
                        .font(.system(size: recording ? 13 : 15, weight: .bold))
                        .foregroundStyle(recording ? Theme.accent : Theme.bg)
                }
            }
            .frame(width: Self.buttonSize, height: Self.buttonSize)
        }
        .buttonStyle(.plain)
        .disabled(preparing)
        .opacity(preparing ? 0.55 : 1)
    }

    // MARK: - The recess

    /// The bar count is only knowable once the recess has a width, so this is
    /// the one place a GeometryReader earns its keep. Everything inside is
    /// driven off `geo.size.width` rather than off a stored constant.
    private var recess: some View {
        GeometryReader { geo in
            ZStack {
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .fill(Theme.bg)

                waveform(count: Self.barCount(recessWidth: geo.size.width))

                playhead
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .frame(height: Self.recessHeight)
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }

    /// `count` bars, oldest first. Padded at the *front* so a short history
    /// grows in from the right rather than being stretched across the recess.
    private func padded(_ count: Int) -> [Double] {
        let tail = levels.suffix(count)
        return Array(repeating: 0, count: max(count - tail.count, 0)) + tail
    }

    /// Centred rather than leading-aligned, which is what puts the middle bar's
    /// centre on the recess's centre line at every width — see the derivation
    /// in the header, and `playhead` below, which relies on it.
    private func waveform(count: Int) -> some View {
        HStack(alignment: .center, spacing: Self.barGap) {
            ForEach(Array(padded(count).enumerated()), id: \.offset) { index, level in
                Capsule()
                    .fill(index <= count / 2 ? Theme.white : Theme.dim.opacity(0.45))
                    .frame(width: Self.barWidth,
                           height: Self.barMin + level * (Self.barMax - Self.barMin))
            }
        }
        .frame(maxWidth: .infinity)
        // One frame of travel per sample, so the wave slides rather than
        // stepping — the same reasoning as TimerBorder's tick animation.
        .animation(.linear(duration: 1.0 / 12), value: levels.count)
    }

    /// A hairline down the centre with a small triangle at its foot, as in the
    /// reference. The triangle is what stops it reading as a divider between
    /// two halves of the recess rather than as a mark on a track.
    private var playhead: some View {
        ZStack(alignment: .bottom) {
            Rectangle()
                .fill(Theme.accent)
                .frame(width: 1)

            Triangle()
                .fill(Theme.accent)
                .frame(width: 9, height: 5)
        }
        .frame(width: 9, height: Self.recessHeight)
        // Simply centred, which the header's derivation shows lands on the
        // middle bar's centre for every width and every odd bar count — so
        // there is no offset here to keep in sync with the waveform.
        .frame(maxWidth: .infinity)
    }
}

/// Apex up, sitting on its base. Three points is less code than coaxing a
/// rotated `Image(systemName:)` into the right size at this scale.
struct Triangle: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.midX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}
