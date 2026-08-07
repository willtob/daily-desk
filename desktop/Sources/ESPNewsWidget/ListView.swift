//
//  ListView.swift — the scrollable column of article cards, best first.
//
//  Straight port of build_list_view() / render_list(): badge and score on one
//  line, headline, rule, source, and a score bar whose width is the story's
//  position in the realistic score band.
//

import SwiftUI

struct ListView: View {
    let articles: [Article]
    let onSelect: (Int) -> Void

    /// Snapshot mode renders without the ScrollView — ImageRenderer does not
    /// lay out scroll content, so a snapshot of the real view would come back
    /// as an empty rectangle. Everything else is identical, which is the
    /// point: the PNG shows the actual cards, not a stand-in.
    var scrollable = true

    var body: some View {
        Scrollable(scrollable) {
            // Not lazy: the digest is capped at 12 articles, so laziness
            // saves nothing and costs the ability to render offscreen.
            VStack(spacing: Theme.pad) {
                ForEach(Array(articles.enumerated()), id: \.element.id) { index, article in
                    ArticleCard(article: article) { onSelect(index) }
                }
            }
            .padding(Theme.pad)
        }
    }
}

/// A ScrollView, or a plain top-aligned clip when rendering offscreen.
struct Scrollable<Content: View>: View {
    private let scrolls: Bool
    private let content: Content

    init(_ scrolls: Bool, @ViewBuilder content: () -> Content) {
        self.scrolls = scrolls
        self.content = content()
    }

    var body: some View {
        if scrolls {
            ScrollView(.vertical, showsIndicators: false) { content }
                .scrollBounceBehavior(.basedOnSize)
        } else {
            // The frame proposes the available height and anchors the content
            // to its top; anything longer spills past the bottom and is
            // clipped, which is what the panel itself shows before you
            // scroll. Without the frame the content is laid out at its
            // natural height and centred, and overruns both ends.
            VStack(spacing: 0) {
                content
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .clipped()
        }
    }
}

private struct ArticleCard: View {
    let article: Article
    let onTap: () -> Void

    @State private var hovering = false

    private var style: AreaStyle { AreaStyle.forArea(article.matchedArea) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {

            // Badge and score share a line — the badge names why the story
            // is here, the number says how strongly.
            HStack(alignment: .firstTextBaseline) {
                Text(style.label)
                    .font(.system(size: Theme.metaSize, weight: .semibold))
                    .tracking(Theme.metaTracking)
                    .foregroundStyle(style.color)
                Spacer(minLength: 4)
                Text(String(format: "%.2f", article.score))
                    .font(.system(size: Theme.metaSize, weight: .medium, design: .monospaced))
                    .foregroundStyle(Theme.dim)
            }

            Text(article.title)
                .font(.system(size: Theme.titleSize, weight: .semibold))
                .foregroundStyle(Theme.white)
                .lineSpacing(Theme.titleLeading)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)

            // Score bar. Always at least a sliver wide, so it reads as a bar
            // rather than as a missing element on a low-scoring story.
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.rule)
                    Capsule()
                        .fill(style.color)
                        .frame(width: max(geo.size.width * Theme.scoreFraction(article.score), 2))
                }
            }
            .frame(height: Theme.barH)

            Text(article.source)
                .font(.system(size: Theme.metaSize))
                .tracking(Theme.metaTracking)
                .foregroundStyle(Theme.dim)
                .lineLimit(1)
        }
        .padding(Theme.cardPad)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: Theme.radius)
                .fill(hovering ? Theme.cardHover : Theme.card)
        )
        .contentShape(RoundedRectangle(cornerRadius: Theme.radius))
        .onTapGesture(perform: onTap)
        .onHover { hovering = $0 }
        // The device shows press feedback; a cursor gets hover, which is the
        // closer equivalent of "this responds to you".
        .animation(.easeOut(duration: 0.12), value: hovering)
    }
}
