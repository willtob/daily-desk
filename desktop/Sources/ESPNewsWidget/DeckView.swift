//
//  DeckView.swift — the digest as a deck of cards, one at a time.
//
//  The list view this replaces needed 700 px of screen to be useful. A deck
//  needs the height of one card, which is what makes the widget small enough
//  to leave on the desktop: the stories that are not on top are still there,
//  they are just behind.
//
//  ── The position trick ────────────────────────────────────────────────────
//
//  The obvious model — an index into `articles` — cannot animate the card you
//  just left, because as soon as the index moves that card is simply not in
//  the deck any more and SwiftUI has nothing to animate *from*.
//
//  So the deck is indexed by a monotonic `position` instead, and the article
//  shown is `articles[position mod count]`. View identity is the position, not
//  the article. Rendering the window `position-1 ... position+3` then gives
//  every card a stable identity across a step:
//
//      position 3 -> 4
//      p=3  slot  0 -> -1     the card you left: softens and drops behind
//      p=4  slot  1 ->  0     rises into place
//      p=5  slot  2 ->  1
//      p=7               3    appears at the back, fades in
//
//  Nothing is inserted or removed at the front, so every one of those is a
//  property change on a view that already exists — which is exactly what
//  SwiftUI knows how to animate. It also makes the deck cycle forever in both
//  directions for free, and a short digest simply repeats.
//

import SwiftUI

struct DeckView: View {

    let articles: [Article]
    /// Monotonic, not an index — see the note above. May go negative.
    let position: Int
    let onOpen: (Int) -> Void

    /// Cards drawn behind the top one. Three is where the stack stops reading
    /// as "more behind this" and starts reading as clutter.
    static let peek = 3

    /// Slow enough to watch the flip, quick enough to press repeatedly. The
    /// low damping gives the card a small settle at the end, which is what
    /// makes it feel like a physical card landing rather than a fade.
    static let step: Animation = .spring(response: 0.44, dampingFraction: 0.74)

    /// The coordinate space the top card's frame is reported in, so a story
    /// can be grown out of exactly where its card was. Named rather than
    /// `.local`, because the measurement is taken in the deck and read in
    /// RootView, two layers up.
    static let space = "widget"

    var body: some View {
        GeometryReader { geo in
            ZStack {
                ForEach(position - 1 ... position + Self.peek, id: \.self) { p in
                    let index = wrap(p)
                    let article = articles[index]
                    let slot = p - position

                    CardFace(article: article) { onOpen(index) }
                        .frame(width: geo.size.width, height: cardHeight(in: geo.size))
                        .modifier(Slot(slot: slot,
                                       tint: AreaStyle.forArea(article.matchedArea).tint))
                        // Only the top card reports a frame, and only when it
                        // is settled in slot 0 — see CardFrameKey.
                        .background(
                            GeometryReader { card in
                                Color.clear.preference(
                                    key: CardFrameKey.self,
                                    value: slot == 0
                                        ? card.frame(in: .named(DeckView.space))
                                        : .zero
                                )
                            }
                        )
                        .zIndex(Slot.z(slot))
                        // Only the top card is live. Without this the peek
                        // cards keep their hit areas and a click near the
                        // bottom edge opens the wrong story.
                        .allowsHitTesting(slot == 0)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .top)
        }
    }

    /// The stack grows downward, so the deck reserves room for the last peek
    /// card's offset. Otherwise the bottom card is clipped by the nav bar.
    private func cardHeight(in size: CGSize) -> CGFloat {
        max(size.height - Slot.dropTotal, 80)
    }

    /// Positive modulo — `%` in Swift keeps the sign of the dividend, so a
    /// plain `p % n` sends you off the front of the array as soon as you page
    /// backwards past the first story.
    private func wrap(_ p: Int) -> Int {
        let n = articles.count
        return ((p % n) + n) % n
    }
}

/// Where the top card is, in the widget's coordinate space.
///
/// This is what lets a story grow out of the card that was tapped rather than
/// arriving from an edge. Every card in the deck writes to the key and all but
/// the top one write `.zero`, so `reduce` keeps the first non-zero value and
/// the winner is whichever card is actually in slot 0.
///
/// Preferring non-zero rather than simply taking the last also covers the
/// moment the deck is empty or rebuilding, when nothing reports a real frame:
/// the value stays `.zero`, and RootView treats that as "no card to grow from"
/// and opens without the expansion instead of animating out of the top-left
/// corner.
struct CardFrameKey: PreferenceKey {
    static var defaultValue: CGRect = .zero

    static func reduce(value: inout CGRect, nextValue: () -> CGRect) {
        let next = nextValue()
        if next != .zero { value = next }
    }
}

// MARK: - Slot geometry

/// Where a card sits, given how far it is from the top of the deck.
///
/// Slot 0 is the card you are reading, 1...3 are stacked behind it, and slot
/// -1 is the card you just left. That last one is the whole animation: it
/// scales down, blurs, swings away on its Y axis and drops behind the deck.
/// Its zIndex is the lowest of all of them on purpose — "flips to the back"
/// means it goes *behind*, not that it flies off the front.
private struct Slot: ViewModifier {

    let slot: Int
    let tint: Color

    /// How far the deepest peek card hangs below the top card.
    ///
    /// This has to beat the shrinkage, not just be positive. A card scaled to
    /// 0.85 about its centre pulls its own bottom edge up by 7.5% of the card
    /// height — around 15 px here — so a drop smaller than that leaves the
    /// whole stack hidden behind the top card and the deck looks like a
    /// single card with an oddly heavy shadow.
    static let dropTotal: CGFloat = 42

    static func z(_ slot: Int) -> Double {
        slot < 0 ? 0 : 40 - Double(min(slot, DeckView.peek))
    }

    func body(content: Content) -> some View {
        content
            // Cards behind the top one are veiled in their own tint: you see
            // the colour of what is coming next, not a smudge of its text.
            // Veiling rather than swapping in a blank view matters — swapping
            // changes the view's type, which SwiftUI cannot cross-fade, so
            // the rising card would pop from empty to full mid-animation.
            .overlay {
                RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                    .fill(tint)
                    .opacity(veil)
            }
            .scaleEffect(scale, anchor: .center)
            .offset(x: dx, y: dy)
            .rotation3DEffect(.degrees(flip), axis: (x: 0, y: 1, z: 0), perspective: 0.65)
            .rotationEffect(.degrees(tilt))
            .blur(radius: blur)
            .opacity(alpha)
    }

    private var depth: Int { min(max(slot, 0), DeckView.peek) }
    private var isGone: Bool { slot < 0 }

    private var scale: Double {
        isGone ? 0.86 : 1 - Double(depth) * 0.05
    }

    private var dy: CGFloat {
        isGone ? -18 : CGFloat(depth) * (Self.dropTotal / CGFloat(DeckView.peek))
    }

    private var dx: CGFloat { isGone ? -24 : 0 }

    /// The flip. 40° with a shallow perspective is the point where it still
    /// reads as a card turning and not as a page in a book.
    private var flip: Double { isGone ? -40 : 0 }

    /// A degree and a half per card, so the stack looks hand-squared rather
    /// than machine-stacked. Any more and it reads as broken layout.
    private var tilt: Double { isGone ? -5 : Double(depth) * 1.5 }

    /// Blur belongs to the card that is leaving and nowhere else. Blurring
    /// the peek cards seems like the obvious way to say "these are behind",
    /// but all you ever see of them is a 15 px ledge along the bottom — blur
    /// turns three ledges into one coloured smear, and it blurs their drop
    /// shadows into a glow. Scale and offset carry the depth on their own.
    private var blur: CGFloat { isGone ? 9 : 0 }

    private var alpha: Double {
        if isGone { return 0 }
        // Beyond the peek limit a card is fully hidden, so a new one at the
        // back fades in rather than appearing.
        if slot > DeckView.peek { return 0 }
        return [1, 1, 0.92, 0.78][depth]
    }

    private var veil: Double { slot >= 1 ? 1 : 0 }
}

// MARK: - The card

/// One story, full bleed in its interest area's colour.
///
/// Ordered the way the reference layouts order it: what this is and how
/// strongly, then the headline doing all the work, then the provenance.
struct CardFace: View {

    let article: Article
    let onOpen: () -> Void

    @State private var hovering = false

    private var style: AreaStyle { AreaStyle.forArea(article.matchedArea) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {

            HStack(alignment: .center, spacing: 6) {
                Text(style.label)
                    .font(.system(size: Theme.metaSize, weight: .bold))
                    .tracking(Theme.metaTracking)
                    .foregroundStyle(style.tint)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Theme.ink))

                Spacer(minLength: 4)

                // The score is the reason this story is on top of the deck,
                // so it gets the weight of a headline number rather than
                // being tucked away as metadata.
                Text(String(format: "%.2f", article.score))
                    .font(.system(size: 13, weight: .bold, design: .monospaced))
                    .foregroundStyle(Theme.ink)
            }

            Spacer(minLength: 10)

            Text(article.title)
                .font(.system(size: Theme.titleSize, weight: .bold))
                .foregroundStyle(Theme.ink)
                .lineSpacing(Theme.titleLeading)
                .multilineTextAlignment(.leading)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)

            if !article.summary.isEmpty {
                // Flattened to plain text on purpose. This is two clipped
                // lines of a much longer summary; a bullet marker or a bolded
                // fragment landing here is noise rather than emphasis, and a
                // list rendered into two lines reads as a broken sentence.
                Text(Paragraphs.plain(article.summary))
                    .font(.system(size: Theme.bodySize))
                    .foregroundStyle(Theme.inkSoft())
                    .lineSpacing(1)
                    .lineLimit(2)
                    .padding(.top, 5)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            scoreBar
                .padding(.bottom, 9)

            HStack(spacing: 6) {
                Text(article.source)
                    .font(.system(size: Theme.metaSize))
                    .tracking(Theme.metaTracking)
                    .foregroundStyle(Theme.inkSoft(Theme.source))
                    .lineLimit(1)

                Spacer(minLength: 4)

                // Affordance only — the whole card is the hit target. It is
                // here because a flat coloured rectangle gives no sign that
                // there is anything underneath it.
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 9, weight: .heavy))
                    .foregroundStyle(style.tint)
                    .frame(width: 20, height: 20)
                    .background(Circle().fill(Theme.ink.opacity(hovering ? 1 : 0.82)))
            }
        }
        .padding(Theme.cardPad)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(
            RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                .fill(style.tint)
                // Two adjacent cards in neighbouring tints — amber behind
                // amber — otherwise merge into one shape at the ledge where
                // they overlap. A dark hairline is enough to keep them
                // countable without reading as a border.
                .overlay(
                    RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                        .stroke(Theme.bg.opacity(0.55), lineWidth: 1)
                )
        )
        // Lifting on hover is the cheapest way to say "this is a stack of
        // things you can pick up".
        .shadow(color: .black.opacity(hovering ? 0.45 : 0.32),
                radius: hovering ? 14 : 9, x: 0, y: hovering ? 6 : 4)
        .contentShape(RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous))
        .onTapGesture(perform: onOpen)
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.14), value: hovering)
    }

    private var scoreBar: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.ink.opacity(0.16))
                Capsule()
                    .fill(Theme.ink.opacity(0.55))
                    // Always at least a sliver, so a low-scoring story reads
                    // as low rather than as a missing element.
                    .frame(width: max(geo.size.width * Theme.scoreFraction(article.score), 3))
            }
        }
        .frame(height: Theme.barH)
    }
}
