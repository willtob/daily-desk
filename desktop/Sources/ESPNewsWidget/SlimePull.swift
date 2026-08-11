//
//  SlimePull.swift — the shape, the physics and the glyphs of the rating pull.
//
//  A port of docs/slime-gesture-reference.html, which is the specification for
//  this and not a description of it. The names below are the names in that
//  file, in the same order, so the two can be read side by side; where a value
//  differs it is because 320 x 44 points of browser became a 70 x 17 badge on a
//  288 x 300 panel, and each of those carries its own note.
//
//  ── What the shape is, and why it is drawn rather than laid out ────────────
//
//  A blob hanging off the badge: a tapering neck from the edge you grabbed to a
//  droplet that follows the cursor. It is *drawn*, in a background that cannot
//  affect layout, precisely because the first version of this gesture was laid
//  out — it stretched the badge's own pill, so the badge grew, so the headline
//  under it moved down while you pulled. Nothing in here returns a size; the
//  badge is the same rectangle at every point of the travel.
//
//  ── The geometry, which is the part worth being careful about ─────────────
//
//  A is the anchor: fixed at the point on the badge edge where the pointer went
//  down. Bottom edge pulling down, top edge pulling up. B is the droplet, and
//  it follows the cursor in *both* axes — it is not pinned to the badge's
//  centre, so a diagonal pull leans.
//
//  `n` is the unit normal to the A->B axis, sign-corrected by the sign of the
//  vertical component. That correction is the whole reason an upward pull works:
//  without it the two sides of the neck swap over as the axis passes horizontal
//  and the neck crosses itself into a bow tie.
//
//  The body is one filled path from the badge edge to a chord across the
//  droplet, with the droplet circle filled on top in the same colour. Two
//  shapes, not one closed path with arcs: the union of a curved neck and a
//  circle has no closed form worth writing, and filling two overlapping
//  subpaths in one path risks the winding rules cancelling the overlap into a
//  hole.
//

import SwiftUI

enum SlimePull {

    // MARK: - Tuning
    //
    // Every one of these is meant to be turned by eye. The reference's value is
    // given wherever this differs from it.

    /// Half-width of the neck where it meets the badge. Reference 50 on a
    /// 320-point bar; this is 15 on a badge about 70 wide, so the root covers
    /// a little under half the badge rather than a third of it. A root scaled
    /// strictly with the bar would be 11 points across and read as a thread.
    static let rootHalfWidth: CGFloat = 26

    /// Reference 26. Not scaled with everything else — this is the floor set by
    /// the glyph inside it, and a droplet under about 16 points across cannot
    /// hold a legible heart. It is therefore proportionally the largest thing
    /// here, and the blob is chunkier than the reference's as a result.
    static let dropRadius: CGFloat = 9.5

    /// Commit threshold. Reference 105.
    ///
    /// Sized from the room above the badge rather than from the reference's
    /// ratio: on the deck the badge sits 46 points below the top of the panel,
    /// and the panel clips. `breakDist + dropRadius` has to fit in that or the
    /// droplet is cut in half exactly when it becomes meaningful.
    static let breakDist: CGFloat = 26

    /// Rubber-band asymptote. Reference 150, keeping the reference's 0.7 ratio
    /// to `breakDist`. At full stretch the droplet's far edge lands at 45.4
    /// points, which is the deck's 46 — so the deck never clips at all, however
    /// hard it is pulled.
    static let maxTravel: CGFloat = 38

    /// The clearance a badge needs above it for an upward pull to be drawn
    /// whole. Derived, not chosen — see DetailView, which is the one place a
    /// badge would otherwise sit too close to the edge of the panel.
    static var headroom: CGFloat { breakDist + dropRadius }

    static let neckTaper: CGFloat = 0.82    // how far the root narrows at full stretch
    static let dropShrink: CGFloat = 0.22
    static let ctrlRoot: CGFloat = 0.95     // bezier handle off the badge edge
    static let ctrlTip: CGFloat = 0.85      // bezier handle off the droplet
    static let chord: CGFloat = 0.92        // where the neck meets the droplet

    static let springK: CGFloat = 0.22
    static let springDamp: CGFloat = 0.72

    /// Below this the blob is not drawn at all, and the spring is finished.
    static let minReach: CGFloat = 1.5
    static let settle: CGFloat = 0.4

    /// Enough travel to mean "pull" rather than "click" — the card underneath
    /// still opens on a tap through the badge.
    static let minimumDistance: CGFloat = 3

    /// How much more vertical than horizontal a pull must be before it counts
    /// as up or down. 1 is a 90-degree cone around vertical: |oy| > |ox|.
    ///
    /// The reference has a bug here — it tests `oy >= 0`, so a dead-horizontal
    /// drag past the threshold records a *like*, which is not a thing anyone
    /// meant to do. A cone was the alternative and this is the same idea with
    /// the angle pinned to the one value that needs no justifying: the pull is
    /// up or down when it is more up-or-down than it is sideways. Raise it for
    /// a narrower cone (2 is about 27 degrees).
    static let commitSlope: CGFloat = 1

    /// The glyph fades in over the last stretch before the threshold, so the
    /// icon arriving *is* the commit indicator. Reference 0.55 and 28.
    static let iconAppearsAt: CGFloat = 0.55
    static let iconFade: CGFloat = 7.5

    /// Glyph widths, in points. The two glyphs are drawn from the reference's
    /// own path data, which gives them very different intrinsic sizes — a
    /// shared scale factor makes the frown a third the width of the heart. So
    /// they are sized by the width they should end up, not by a factor.
    static let dropletGlyph: CGFloat = 7.5
    static let badgeGlyph: CGFloat = 9

    /// The frown's mouth, in final points rather than scaled with the glyph:
    /// at these sizes a proportional stroke turns the mouth into a blob.
    static let frownStroke: CGFloat = 1.1

    /// Half the rim's stroke width. The rim is stroked at twice this and then
    /// half of it is covered by the fill — see `SlimeBlob`.
    static let rim: CGFloat = 1

    // MARK: - Rubber band

    /// Travel approaches `maxTravel` asymptotically, one-to-one at the origin.
    /// Applied to the vector, not per axis, so resistance does not depend on
    /// the direction of the pull.
    static func band(_ translation: CGSize) -> CGSize {
        let distance = hypot(translation.width, translation.height)
        guard distance > 0.001 else { return .zero }
        let eased = maxTravel * (1 - exp(-distance / maxTravel))
        let f = eased / distance
        return CGSize(width: translation.width * f, height: translation.height * f)
    }

    // MARK: - The shape

    /// Everything the blob's parts need, worked out once.
    ///
    /// Nil below `minReach`, which is what "there is no blob" looks like — the
    /// shapes return empty paths and nothing is drawn.
    struct Geometry {
        let anchor: CGPoint         // A, on the badge edge
        let drop: CGPoint           // B, under the cursor
        let normal: CGVector        // unit normal to A->B, sign-corrected
        let axis: CGVector          // unit A->B
        let reach: CGFloat
        let rootHalfWidth: CGFloat
        let radius: CGFloat
        let down: Bool
    }

    static func geometry(bar: CGRect, grabX: CGFloat?, offset: CGSize) -> Geometry? {
        let ox = offset.width, oy = offset.height
        let reach = hypot(ox, oy)
        guard reach >= minReach else { return nil }

        let down = oy >= 0
        let ay = down ? bar.maxY : bar.minY
        // No grab point means nothing has been pulled by hand: hang it from
        // the middle. Only the frozen-pull renderer ever takes that branch.
        let ax = grabX ?? bar.midX
        let anchor = CGPoint(x: ax, y: ay)
        let drop = CGPoint(x: ax + ox, y: ay + oy)

        // The sign correction. `sgn` follows the vertical component, so the
        // normal keeps pointing the same way round the badge whichever edge
        // the pull started from.
        let d = reach
        let sgn: CGFloat = oy >= 0 ? 1 : -1
        let normal = CGVector(dx: -oy / d * sgn, dy: ox / d * sgn)
        let axis = CGVector(dx: ox / d, dy: oy / d)

        let p = min(1, d / maxTravel)

        return Geometry(
            anchor: anchor,
            drop: drop,
            normal: normal,
            axis: axis,
            reach: d,
            rootHalfWidth: rootHalfWidth * (1 - neckTaper * p),
            radius: dropRadius * (1 - dropShrink * p),
            down: down
        )
    }

    /// The filled body, from the badge edge to a chord across the droplet.
    static func neck(_ g: Geometry) -> Path {
        var path = Path()
        let (e1, e2, c1, c4) = root(g)
        let (q1, q2, c2, c3) = tip(g)

        path.move(to: e1)
        path.addCurve(to: q1, control1: c1, control2: c2)
        path.addLine(to: q2)
        path.addCurve(to: e2, control1: c3, control2: c4)
        path.closeSubpath()
        return path
    }

    /// The neck's two flanks, open, for the rim.
    ///
    /// Open rather than the closed body: the chord across the root is not part
    /// of the blob's outline — the badge continues there — and stroking it
    /// would draw a line along the badge's own edge.
    static func neckOutline(_ g: Geometry) -> Path {
        var path = Path()
        let (e1, e2, c1, c4) = root(g)
        let (q1, q2, c2, c3) = tip(g)

        path.move(to: e1)
        path.addCurve(to: q1, control1: c1, control2: c2)
        path.move(to: q2)
        path.addCurve(to: e2, control1: c3, control2: c4)
        return path
    }

    static func droplet(_ g: Geometry) -> Path {
        Path(ellipseIn: CGRect(x: g.drop.x - g.radius, y: g.drop.y - g.radius,
                               width: g.radius * 2, height: g.radius * 2))
    }

    private static func root(_ g: Geometry) -> (CGPoint, CGPoint, CGPoint, CGPoint) {
        let w = g.rootHalfWidth
        let ay = g.anchor.y
        let sgn: CGFloat = g.down ? 1 : -1
        let k = g.reach * 0.5
        let e1 = CGPoint(x: g.anchor.x - w, y: ay)
        let e2 = CGPoint(x: g.anchor.x + w, y: ay)
        let cy = ay + sgn * k * ctrlRoot
        return (e1, e2,
                CGPoint(x: e1.x + g.normal.dx * w * 0.25, y: cy),
                CGPoint(x: e2.x - g.normal.dx * w * 0.25, y: cy))
    }

    private static func tip(_ g: Geometry) -> (CGPoint, CGPoint, CGPoint, CGPoint) {
        let k = g.reach * 0.5 * ctrlTip
        let r = g.radius * chord
        let q1 = CGPoint(x: g.drop.x + g.normal.dx * r, y: g.drop.y + g.normal.dy * r)
        let q2 = CGPoint(x: g.drop.x - g.normal.dx * r, y: g.drop.y - g.normal.dy * r)
        return (q1, q2,
                CGPoint(x: q1.x - g.axis.dx * k, y: q1.y - g.axis.dy * k),
                CGPoint(x: q2.x - g.axis.dx * k, y: q2.y - g.axis.dy * k))
    }

    // MARK: - Committing

    /// Whether a pull this far, in this direction, is a verdict yet.
    static func commits(_ offset: CGSize) -> Bool {
        hypot(offset.width, offset.height) > breakDist
            && abs(offset.height) > commitSlope * abs(offset.width)
    }

    static func iconOpacity(reach: CGFloat) -> Double {
        let start = breakDist * iconAppearsAt
        guard reach > start else { return 0 }
        return Double(min(1, (reach - start) / iconFade))
    }

    // MARK: - Glyphs
    //
    // The reference's own path data, centred on a point and sized by the width
    // it should occupy. Both readers of this — the droplet and the badge —
    // draw the same two shapes, so what appears mid-pull is exactly what stays
    // behind afterwards.

    /// Intrinsic widths of the path data below, which is what `width` scales.
    private static let heartWidth: CGFloat = 11.2   // ±5.6
    private static let frownWidth: CGFloat = 5.2    // ±2.6

    static func heart(at c: CGPoint, width: CGFloat) -> Path {
        let s = width / heartWidth
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: c.x + x * s, y: c.y + y * s)
        }
        var path = Path()
        path.move(to: p(0, 4.2))
        path.addCurve(to: p(-2.2, -4.2), control1: p(-5.6, 0.2), control2: p(-5.6, -4.2))
        path.addCurve(to: p(0, -2.2),    control1: p(-0.8, -4.2), control2: p(0, -3.1))
        path.addCurve(to: p(2.2, -4.2),  control1: p(0, -3.1),    control2: p(0.8, -4.2))
        path.addCurve(to: p(0, 4.2),     control1: p(5.6, -4.2),  control2: p(5.6, 0.2))
        path.closeSubpath()
        return path
    }

    static func frownEyes(at c: CGPoint, width: CGFloat) -> Path {
        let s = width / frownWidth
        var path = Path()
        for dx in [CGFloat(-2), 2] {
            let r = 0.7 * s
            path.addEllipse(in: CGRect(x: c.x + dx * s - r, y: c.y - 1.6 * s - r,
                                       width: r * 2, height: r * 2))
        }
        return path
    }

    static func frownMouth(at c: CGPoint, width: CGFloat) -> Path {
        let s = width / frownWidth
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: c.x + x * s, y: c.y + y * s)
        }
        var path = Path()
        path.move(to: p(-2.6, 2.8))
        path.addQuadCurve(to: p(2.6, 2.8), control: p(0, 0.6))
        return path
    }
}

// MARK: - Shapes

/// Each of these takes the badge's own rectangle as `rect` — they are drawn in
/// a background, so that is exactly what SwiftUI hands them — and every one is
/// free to return a path well outside it. Shapes are not clipped to their
/// frame, which is what lets the blob hang off the badge without a single
/// layout-affecting modifier anywhere in the stack.

struct SlimeNeck: Shape {
    let offset: CGSize
    let grabX: CGFloat?
    var outline = false

    func path(in rect: CGRect) -> Path {
        guard let g = SlimePull.geometry(bar: rect, grabX: grabX, offset: offset) else {
            return Path()
        }
        return outline ? SlimePull.neckOutline(g) : SlimePull.neck(g)
    }
}

struct SlimeDroplet: Shape {
    let offset: CGSize
    let grabX: CGFloat?

    func path(in rect: CGRect) -> Path {
        guard let g = SlimePull.geometry(bar: rect, grabX: grabX, offset: offset) else {
            return Path()
        }
        return SlimePull.droplet(g)
    }
}

/// A heart or a frown, drawn either in the droplet or in the badge.
///
/// One shape for both jobs so the glyph cannot drift between them: `anchor`
/// decides whether it is placed at the droplet the pull is currently holding,
/// or in the middle of whatever frame it is given.
struct SlimeGlyph: Shape {

    enum Mark { case like, dislike }
    enum Anchor { case droplet(offset: CGSize, grabX: CGFloat?), centre }
    enum Part { case fill, stroke }

    let mark: Mark
    let part: Part
    var anchor: Anchor = .centre
    var width: CGFloat = SlimePull.badgeGlyph

    func path(in rect: CGRect) -> Path {
        let centre: CGPoint
        switch anchor {
        case let .droplet(offset, grabX):
            guard let g = SlimePull.geometry(bar: rect, grabX: grabX, offset: offset) else {
                return Path()
            }
            centre = g.drop
        case .centre:
            centre = CGPoint(x: rect.midX, y: rect.midY)
        }

        switch (mark, part) {
        case (.like, .fill):     return SlimePull.heart(at: centre, width: width)
        case (.like, .stroke):   return Path()
        case (.dislike, .fill):  return SlimePull.frownEyes(at: centre, width: width)
        case (.dislike, .stroke): return SlimePull.frownMouth(at: centre, width: width)
        }
    }
}

/// A heart or a frown as a view, for the badge's own mark. Two parts because
/// the frown's eyes are filled and its mouth is stroked.
struct SlimeMark: View {
    let mark: SlimeGlyph.Mark
    let color: Color
    var width: CGFloat = SlimePull.badgeGlyph

    var body: some View {
        ZStack {
            SlimeGlyph(mark: mark, part: .fill, width: width).fill(color)
            SlimeGlyph(mark: mark, part: .stroke, width: width)
                .stroke(color, style: StrokeStyle(lineWidth: SlimePull.frownStroke,
                                                  lineCap: .round))
        }
    }
}

// MARK: - The blob

/// The whole body, drawn behind the badge's text and over its pill.
struct SlimeBlob: View {

    let offset: CGSize
    let grabX: CGFloat?
    let tint: Color

    private var reach: CGFloat { hypot(offset.width, offset.height) }
    private var mark: SlimeGlyph.Mark { offset.height >= 0 ? .like : .dislike }

    var body: some View {
        ZStack {
            // Rim first, fill on top, and that ordering is the trick.
            //
            // The blob is a neck and a circle that overlap, and there is no
            // single path to stroke. Stroking each of them separately draws
            // their shared boundary — a chord across the droplet and an arc
            // across the neck — straight through the middle of what is meant
            // to read as one body. Drawing both strokes first and then filling
            // both shapes over them covers every internal edge *and* the inner
            // half of each stroke, leaving one clean outline around the union.
            // Hence the doubled line width: half of it is thrown away.
            //
            // The rim exists because an upward pull leaves the bright card and
            // crosses onto the near-black shell, where a near-black blob is
            // invisible exactly when it matters. Same colour and same weight as
            // the badge's own — see BadgePill.
            let rimColor = tint.opacity(BadgePill.rim)
            SlimeNeck(offset: offset, grabX: grabX, outline: true)
                .stroke(rimColor, lineWidth: SlimePull.rim * 2)
            SlimeDroplet(offset: offset, grabX: grabX)
                .stroke(rimColor, lineWidth: SlimePull.rim * 2)

            SlimeNeck(offset: offset, grabX: grabX).fill(Theme.ink)
            SlimeDroplet(offset: offset, grabX: grabX).fill(Theme.ink)

            glyph.opacity(SlimePull.iconOpacity(reach: reach))
        }
    }

    /// The heart or frown riding in the droplet, in the area's tint.
    private var glyph: some View {
        let anchor = SlimeGlyph.Anchor.droplet(offset: offset, grabX: grabX)
        return ZStack {
            SlimeGlyph(mark: mark, part: .fill, anchor: anchor, width: SlimePull.dropletGlyph)
                .fill(tint)
            SlimeGlyph(mark: mark, part: .stroke, anchor: anchor, width: SlimePull.dropletGlyph)
                .stroke(tint, style: StrokeStyle(lineWidth: SlimePull.frownStroke, lineCap: .round))
        }
    }
}

// MARK: - Motion

/// The pull's offset over time: driven by the cursor while dragging, by a
/// spring afterwards.
///
/// A fixed-step integrator on a 60 Hz timer, which is what the reference does
/// (`v += -o * K; v *= DAMP; o += v` per animation frame) and is why SPRING_K
/// and SPRING_DAMP survive the port as themselves. Handing the return to
/// `withAnimation(.spring(response:dampingFraction:))` instead would have been
/// less code and would have quietly replaced both constants with two others
/// that mean something different — and the brief was that these are the numbers
/// to turn.
///
/// The timer runs only while the spring is settling, about half a second, and
/// it is added to the common run-loop modes so a pull released while a menu is
/// tracking still comes home instead of freezing mid-air.
@MainActor
final class SlimeMotion: ObservableObject {

    @Published private(set) var offset: CGSize = .zero
    /// Where on the badge the pointer went down. The anchor stays there for the
    /// life of the pull, which is what makes the blob hang from the point you
    /// grabbed rather than from the middle.
    @Published private(set) var grabX: CGFloat?

    private var velocity: CGSize = .zero
    private var timer: Timer?

    /// A pull frozen at a fixed cursor travel, for looking at the shape without
    /// a hand on the trackpad:
    ///
    ///     ESP_NEWS_SLIME=8,60 swift run ESPNewsWidget --snapshot out/ --offline
    ///
    /// The value is raw travel in points, before the rubber band, so it reads
    /// the same as a drag. Every live badge freezes at it, hanging from its
    /// centre because there is no grab point. Unset in normal use, and the
    /// whole feature is these six lines — see Snapshot for why looking at the
    /// UI is worth being able to do without launching it.
    init() {
        let parts = (ProcessInfo.processInfo.environment["ESP_NEWS_SLIME"] ?? "")
            .split(separator: ",").compactMap { Double($0) }
        guard parts.count == 2 else { return }
        offset = SlimePull.band(CGSize(width: parts[0], height: parts[1]))
    }

    func drag(to translation: CGSize, grabX: CGFloat) {
        stop()
        self.grabX = grabX
        offset = SlimePull.band(translation)
    }

    /// Let go. No fling: the reference zeroes the velocity on release, so the
    /// blob is always thrown home by the spring rather than by how fast the
    /// cursor happened to be moving.
    func release() {
        velocity = .zero
        guard offset != .zero else { return }
        let timer = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    /// Home immediately, with no spring — for the article under the badge being
    /// swapped out mid-pull by a page or a poll.
    func cancel() {
        stop()
        velocity = .zero
        offset = .zero
    }

    private func tick() {
        velocity.width += (0 - offset.width) * SlimePull.springK
        velocity.width *= SlimePull.springDamp
        offset.width += velocity.width

        velocity.height += (0 - offset.height) * SlimePull.springK
        velocity.height *= SlimePull.springDamp
        offset.height += velocity.height

        if hypot(offset.width, offset.height) < SlimePull.settle,
           hypot(velocity.width, velocity.height) < SlimePull.settle {
            cancel()
        }
    }

    private func stop() {
        timer?.invalidate()
        timer = nil
    }
}
