//
//  DetailView.swift — one story, opened out of its card.
//
//  Same colour as the card it came from, edge to edge. That is the whole
//  continuity trick: the card grows into the story rather than being replaced
//  by a screen, so on a panel this small you never lose track of where you
//  were. The reference design does the same thing — tap a yellow card, get a
//  yellow story.
//

import AppKit
import SwiftUI

struct DetailView: View {
    let article: Article
    let index: Int
    let client: NewsClient

    let onBack: () -> Void

    @ObservedObject var audio: AudioPlayer

    /// Nil in snapshots, which renders the badge exactly as it was before the
    /// pull existed. See AreaBadge.
    var feedback: FeedbackStore?

    /// The panel, so the strip beside the badge can move the window.
    ///
    /// An open story covers the header, which is the only other place a drag
    /// moves the panel — without this the widget would be pinned to the desk
    /// for as long as a story is open. Nil in snapshots.
    var panel: PanelController?

    /// See Scrollable — snapshot mode only.
    var scrollable = true

    private var style: AreaStyle { AreaStyle.forArticle(article) }

    private var isThisPlaying: Bool { audio.playingIndex == index }

    var body: some View {
        VStack(spacing: 0) {

            // Back and badge share the top row, which keeps the story's own
            // headline as the first thing you read.
            HStack(spacing: 8) {
                Button(action: onBack) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 10, weight: .heavy))
                        .foregroundStyle(style.tint)
                        .frame(width: 22, height: 22)
                        .background(Circle().fill(Theme.ink))
                }
                .buttonStyle(.plain)

                // The gap between the back button and the badge is the story
                // view's title bar. Deliberately a region of its own rather
                // than a gesture on the whole row: the badge is in this row,
                // and a drag on the row would be the same argument the window
                // drag has just been taken out of.
                if let panel {
                    Color.clear
                        .frame(maxWidth: .infinity, maxHeight: 22)
                        .contentShape(Rectangle())
                        .gesture(panel.dragGesture())
                        // A Spacer yields to the views around it; a Color does
                        // not, and this one was taking its width out of the
                        // badge — on a 252-point panel the label wrapped to two
                        // lines and made the row taller than the badge it is
                        // named for. Negative priority hands the strip whatever
                        // is left after the badge and the score have theirs.
                        .layoutPriority(-1)
                } else {
                    Spacer(minLength: 4)
                }

                // Same badge and the same pull as the deck. An open story is
                // where a verdict is most likely to be formed — it is the only
                // view where the summary has been read — so leaving the gesture
                // behind on the card would put it in the wrong place.
                AreaBadge(article: article, feedback: feedback, client: client)

                Text(String(format: "%.2f", article.score))
                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                    .foregroundStyle(Theme.ink)
            }
            .padding(.horizontal, Theme.cardPad)
            // Headroom, not styling. An upward pull anchors on the top edge of
            // the badge and reaches `SlimePull.headroom` above it; this row sat
            // twelve points from the top of the panel, which clips a dislike in
            // half exactly as it commits. The deck has forty-six points of room
            // above its badge and needs no help — this is the one view where a
            // badge would otherwise be jammed against the edge of the window.
            .padding(.top, SlimePull.headroom)
            .padding(.bottom, 8)
            // As on the card: the blob is drawn over the story, not under it.
            .zIndex(1)

            Scrollable(scrollable) {
                VStack(alignment: .leading, spacing: 8) {

                    Text(article.title)
                        .font(.system(size: Theme.displaySize, weight: .bold))
                        .foregroundStyle(Theme.ink)
                        .lineSpacing(Theme.titleLeading)
                        .fixedSize(horizontal: false, vertical: true)

                    Text(article.source)
                        .font(.system(size: Theme.metaSize))
                        .tracking(Theme.metaTracking)
                        .foregroundStyle(Theme.inkSoft(Theme.source))

                    Rectangle()
                        .fill(Theme.ink.opacity(Theme.hairline))
                        .frame(height: 1)
                        .padding(.vertical, 2)

                    // The empty case is real: a fetch failure leaves the RSS
                    // summary, and some feeds ship nothing worth showing.
                    if article.summary.isEmpty {
                        Text("(no summary in the feed)")
                            .font(.system(size: Theme.bodySize))
                            .foregroundStyle(Theme.inkSoft(Theme.source))
                    } else {
                        // Blocks rather than one Text: the summary may carry
                        // bullets, and a marker has to sit outside the text
                        // column for wrapped lines to indent correctly.
                        SummaryBody(text: article.summary,
                                    color: Theme.ink.opacity(Theme.bodyInk))
                    }

                    // A Button rather than a Link: Link has no useful
                    // behaviour for a malformed url, and ImageRenderer draws
                    // it as a placeholder, which makes snapshots misleading.
                    if let url = URL(string: article.url) {
                        Button {
                            NSWorkspace.shared.open(url)
                        } label: {
                            HStack(spacing: 4) {
                                Text("READ THE ARTICLE")
                                    .tracking(Theme.metaTracking)
                                Image(systemName: "arrow.up.right")
                            }
                            .font(.system(size: Theme.metaSize, weight: .bold))
                            .foregroundStyle(Theme.ink)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Capsule().stroke(Theme.ink.opacity(0.45), lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .padding(.top, 2)
                    }
                }
                .padding(.horizontal, Theme.cardPad)
                .padding(.bottom, Theme.pad)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: .infinity, alignment: .top)
            // Opening a story always starts at the top, wherever the last one
            // was left. `id:` forces a fresh scroll view per article, which is
            // the cheapest way to get that.
            .id(article.id)

            listenButton
        }
        .background(style.tint)
    }

    private var listenButton: some View {
        Button {
            audio.toggle(index: index, using: client)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: buttonIcon)
                Text(buttonLabel).tracking(Theme.metaTracking)
            }
            .font(.system(size: Theme.metaSize, weight: .bold))
            .foregroundStyle(style.tint)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .background(audio.failed && !isThisPlaying
                        ? Theme.ink.opacity(0.72)
                        : Theme.ink)
        }
        .buttonStyle(.plain)
    }

    private var buttonIcon: String {
        if audio.isLoading            { return "waveform" }
        if isThisPlaying              { return "stop.fill" }
        if audio.failed               { return "exclamationmark.triangle" }
        return "play.fill"
    }

    private var buttonLabel: String {
        if audio.isLoading            { return "SYNTHESIZING…" }
        if isThisPlaying              { return "STOP" }
        if audio.failed               { return "AUDIO FAILED" }
        return "LISTEN"
    }
}
