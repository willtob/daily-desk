//
//  DetailView.swift — one story: headline, provenance, summary, narration.
//
//  Port of build_detail_view(). The Listen button says what it will do next
//  rather than what is happening, same as the device's.
//

import AppKit
import SwiftUI

struct DetailView: View {
    let article: Article
    let index: Int
    let client: NewsClient

    let onBack: () -> Void

    @ObservedObject var audio: AudioPlayer

    /// See ListView.scrollable — snapshot mode only.
    var scrollable = true

    private var style: AreaStyle { AreaStyle.forArea(article.matchedArea) }

    private var isThisPlaying: Bool { audio.playingIndex == index }

    var body: some View {
        VStack(spacing: 0) {

            // Back row. The device gets a swipe; here the button carries the
            // same job and the Escape key does it without aiming.
            HStack(spacing: 8) {
                Button(action: onBack) {
                    HStack(spacing: 4) {
                        Image(systemName: "chevron.left")
                        Text("BACK")
                            .tracking(Theme.metaTracking)
                    }
                    .font(.system(size: Theme.metaSize, weight: .semibold))
                    .foregroundStyle(Theme.dim)
                }
                .buttonStyle(.plain)

                Spacer()

                Text(style.label)
                    .font(.system(size: Theme.metaSize, weight: .semibold))
                    .tracking(Theme.metaTracking)
                    .foregroundStyle(style.color)
            }
            .padding(.horizontal, Theme.pad)
            .padding(.vertical, 8)

            Divider().overlay(Theme.rule)

            Scrollable(scrollable) {
                VStack(alignment: .leading, spacing: 10) {

                    Text(article.title)
                        .font(.system(size: Theme.displaySize, weight: .bold))
                        .foregroundStyle(Theme.white)
                        .lineSpacing(Theme.titleLeading)
                        .fixedSize(horizontal: false, vertical: true)

                    Text("\(article.source)  |  \(String(format: "%.3f", article.score))")
                        .font(.system(size: Theme.metaSize))
                        .tracking(Theme.metaTracking)
                        .foregroundStyle(Theme.dim)

                    Divider().overlay(Theme.rule)

                    // The empty case is real: a fetch failure leaves the RSS
                    // summary, and some feeds ship nothing worth showing.
                    Text(article.summary.isEmpty ? "(no summary in the feed)" : article.summary)
                        .font(.system(size: Theme.bodySize))
                        .foregroundStyle(Theme.white.opacity(0.92))
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
                            .font(.system(size: Theme.metaSize, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                        }
                        .buttonStyle(.plain)
                        .padding(.top, 2)
                    }
                }
                .padding(Theme.pad)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: .infinity, alignment: .top)
            // Opening a story always starts at the top, wherever the last one
            // was left. `id:` forces a fresh scroll view per article, which is
            // the cheapest way to get that.
            .id(article.id)

            listenButton
        }
        .background(Theme.bg)
    }

    private var listenButton: some View {
        Button {
            audio.toggle(index: index, using: client)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: buttonIcon)
                Text(buttonLabel).tracking(Theme.metaTracking)
            }
            .font(.system(size: Theme.metaSize, weight: .semibold))
            .foregroundStyle(audio.failed && !isThisPlaying ? Theme.accent : Theme.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .background(isThisPlaying ? Theme.accent.opacity(0.35) : Theme.cardHover)
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
