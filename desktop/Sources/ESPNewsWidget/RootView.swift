//
//  RootView.swift — header, and the list/detail sheet swap.
//
//  Two views built once and slid over each other, same as the device: detail
//  comes in from the right over a list that stays put underneath. On a panel
//  this narrow, a cut to a new screen loses the sense of where you were.
//

import AppKit
import SwiftUI

struct RootView: View {

    @ObservedObject var store: DigestStore
    @ObservedObject var controller: PanelController
    @StateObject private var audio = AudioPlayer()

    @State private var selected: Int?

    /// Matches SLIDE_MS in news_ui.cpp.
    private static let slide: Animation = .easeOut(duration: 0.18)

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(Theme.rule)

            GeometryReader { geo in
                ZStack {
                    listLayer
                    detailLayer(width: geo.size.width)
                }
            }
        }
        .background(Theme.bg)
        .foregroundStyle(Theme.white)
        .task {
            store.startPolling()
        }
        // Escape backs out of a story; the arrows walk the digest, which is
        // the keyboard equivalent of the device's left/right swipes.
        .onKeyPress(.escape) {
            guard selected != nil else { return .ignored }
            back()
            return .handled
        }
        .onKeyPress(.leftArrow)  { step(-1) }
        .onKeyPress(.rightArrow) { step(1) }
    }

    // MARK: - Layers

    private var listLayer: some View {
        Group {
            if store.articles.isEmpty {
                emptyState
            } else {
                ListView(articles: store.articles) { index in
                    withAnimation(Self.slide) { selected = index }
                }
            }
        }
    }

    @ViewBuilder
    private func detailLayer(width: CGFloat) -> some View {
        if let selected, selected < store.articles.count {
            DetailView(
                article: store.articles[selected],
                index: selected,
                client: store.client,
                onBack: back,
                audio: audio
            )
            .transition(.move(edge: .trailing))
            .zIndex(1)
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 8) {
            Text("NEWS")
                .font(.system(size: Theme.metaSize + 1, weight: .bold))
                .tracking(2)
                .foregroundStyle(Theme.accent)

            Spacer(minLength: 4)

            VStack(alignment: .trailing, spacing: 1) {
                Text(store.statusText)
                    .font(.system(size: Theme.metaSize))
                    .foregroundStyle(store.fetchFailed ? Theme.accent : Theme.dim)
                if let age = store.ageText, !store.rebuilding {
                    Text(age)
                        .font(.system(size: Theme.metaSize - 1))
                        .foregroundStyle(Theme.dim.opacity(0.7))
                }
            }
            .lineLimit(1)

            refreshButton
        }
        .padding(.horizontal, Theme.pad)
        .frame(height: Theme.headerH)
        .contentShape(Rectangle())
        .contextMenu { headerMenu }
    }

    private var refreshButton: some View {
        Button {
            Task { await store.rebuild() }
        } label: {
            Image(systemName: "arrow.clockwise")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(store.rebuilding ? Theme.dim : Theme.white)
                .rotationEffect(.degrees(store.rebuilding ? 360 : 0))
                .animation(
                    store.rebuilding
                        ? .linear(duration: 1).repeatForever(autoreverses: false)
                        : .default,
                    value: store.rebuilding
                )
        }
        .buttonStyle(.plain)
        .disabled(store.rebuilding)
        .help("Re-run the pipeline (fetches new articles, ~30 s)")
    }

    @ViewBuilder
    private var headerMenu: some View {
        Button("Refresh now") { Task { await store.rebuild() } }
            .disabled(store.rebuilding)
        Button("Reload digest") { Task { await store.load() } }
        Divider()
        Picker("Placement", selection: $controller.placement) {
            ForEach(PanelController.Placement.allCases, id: \.self) { placement in
                Text(placement.title).tag(placement)
            }
        }
        Divider()
        Button("Quit") { NSApplication.shared.terminate(nil) }
    }

    // MARK: - Empty and error states

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: store.fetchFailed ? "wifi.exclamationmark" : "newspaper")
                .font(.system(size: 24))
                .foregroundStyle(Theme.dim)
            Text(store.fetchFailed ? "Can't reach the backend" : "No stories yet")
                .font(.system(size: Theme.bodySize, weight: .semibold))
                .foregroundStyle(Theme.white)
            // The real cause is almost always one of two things: the server
            // isn't running, or it's on a different port than this panel
            // expects. Say both rather than "error".
            Text(store.lastError ?? "Start it with `uv run esp-serve --port 8010`")
                .font(.system(size: Theme.metaSize))
                .foregroundStyle(Theme.dim)
                .multilineTextAlignment(.center)
            Text(store.baseURL.absoluteString)
                .font(.system(size: Theme.metaSize, design: .monospaced))
                .foregroundStyle(Theme.dim.opacity(0.6))
            Button("Retry") { Task { await store.load() } }
                .buttonStyle(.plain)
                .font(.system(size: Theme.metaSize, weight: .semibold))
                .foregroundStyle(Theme.accent)
                .padding(.top, 4)
        }
        .padding(Theme.pad * 2)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Navigation

    private func back() {
        // Leaving a story stops its narration — otherwise audio keeps playing
        // over a list you are already scrolling through.
        audio.stop()
        withAnimation(Self.slide) { selected = nil }
    }

    private func step(_ delta: Int) -> KeyPress.Result {
        guard let current = selected else { return .ignored }
        let next = current + delta
        guard next >= 0, next < store.articles.count else {
            if delta < 0 { back(); return .handled }   // left off the start = back
            return .handled
        }
        audio.stop()
        withAnimation(Self.slide) { selected = next }
        return .handled
    }
}
