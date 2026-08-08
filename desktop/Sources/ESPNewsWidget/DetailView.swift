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

    /// See Scrollable — snapshot mode only.
    var scrollable = true

    private var style: AreaStyle { AreaStyle.forArea(article.matchedArea) }

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

                Spacer(minLength: 4)

                Text(style.label)
                    .font(.system(size: Theme.metaSize, weight: .bold))
                    .tracking(Theme.metaTracking)
                    .foregroundStyle(style.tint)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Theme.ink))

                Text(String(format: "%.2f", article.score))
                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                    .foregroundStyle(Theme.ink)
            }
            .padding(.horizontal, Theme.cardPad)
            .padding(.top, Theme.pad)
            .padding(.bottom, 8)

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
                        .foregroundStyle(Theme.inkSoft(0.62))

                    Rectangle()
                        .fill(Theme.ink.opacity(0.18))
                        .frame(height: 1)
                        .padding(.vertical, 2)

                    // The empty case is real: a fetch failure leaves the RSS
                    // summary, and some feeds ship nothing worth showing.
                    Text(article.summary.isEmpty ? "(no summary in the feed)" : article.summary)
                        .font(.system(size: Theme.bodySize))
                        .foregroundStyle(Theme.inkSoft(0.82))
                        .lineSpacing(Theme.bodyLeading)
                        .fixedSize(horizontal: false, vertical: true)

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
