//
//  RootView.swift — header, deck, nav, and the story that opens over them.
//
//  Three bands and an overlay. The header and the nav bar are fixed so the
//  controls never move; everything that animates happens in the deck between
//  them. That split is deliberate — an earlier sketch put the next button on
//  the card itself, which meant the button flew away and came back every time
//  you pressed it, and paging through six stories became a game of chase.
//

import AppKit
import SwiftUI

struct RootView: View {

    @ObservedObject var store: DigestStore
    @ObservedObject var controller: PanelController
    @StateObject private var audio = AudioPlayer()

    /// Monotonic deck position — see DeckView. Not an index into `articles`.
    @State private var position = 0
    /// Which story is open over the deck, if any.
    @State private var openIndex: Int?

    var body: some View {
        ZStack {
            Theme.bg

            VStack(spacing: 0) {
                header
                deck
                nav
            }

            if let openIndex, openIndex < store.articles.count {
                DetailView(
                    article: store.articles[openIndex],
                    index: openIndex,
                    client: store.client,
                    onBack: close,
                    audio: audio
                )
                // Up from the deck and back down into it — the story comes
                // out of the card rather than replacing the screen.
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .zIndex(2)
            }
        }
        // The window itself is transparent; this is the widget's real edge.
        .clipShape(RoundedRectangle(cornerRadius: Theme.shellRadius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.shellRadius, style: .continuous)
                .stroke(Color.white.opacity(0.07), lineWidth: 1)
        )
        // Drag from anywhere, the way it did when AppKit was moving the
        // window — but as a gesture, so clicks still reach the deck.
        //
        // minimumDistance is what keeps the two apart: a click that never
        // moves 6 points is a click, and the card's own tap gesture wins it
        // because a child's gesture takes priority over a parent's.
        .gesture(
            DragGesture(minimumDistance: 6)
                .onChanged { _ in controller.dragChanged() }
                .onEnded   { _ in controller.dragEnded() }
        )
        .task { store.startPolling() }
        .onKeyPress(.escape) {
            guard openIndex != nil else { return .ignored }
            close()
            return .handled
        }
        .onKeyPress(.leftArrow)  { step(-1); return .handled }
        .onKeyPress(.rightArrow) { step(1);  return .handled }
        .onKeyPress(.space)      { open();   return .handled }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 6) {
            Text("NEWS")
                .font(.system(size: Theme.metaSize + 1, weight: .black))
                .tracking(2)
                .foregroundStyle(Theme.accent)

            Spacer(minLength: 4)

            Text(store.rebuilding ? store.statusText : (store.ageText ?? store.statusText))
                .font(.system(size: Theme.metaSize))
                .foregroundStyle(store.fetchFailed ? Theme.accent : Theme.dim)
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
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(store.rebuilding ? Theme.accent : Theme.dim)
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

    // MARK: - Deck

    @ViewBuilder
    private var deck: some View {
        if store.articles.isEmpty {
            emptyState
        } else {
            DeckView(articles: store.articles, position: position) { index in
                openIndex = index
            }
            .padding(.horizontal, Theme.pad)
            .padding(.bottom, 2)
            .animation(DeckView.step, value: position)
        }
    }

    // MARK: - Nav

    private var nav: some View {
        HStack(spacing: 8) {
            navButton("chevron.left", filled: false) { step(-1) }

            Spacer(minLength: 4)

            Text(counter)
                .font(.system(size: Theme.metaSize, weight: .medium, design: .monospaced))
                .foregroundStyle(Theme.dim)
                .contentTransition(.numericText())

            Spacer(minLength: 4)

            navButton("chevron.right", filled: true) { step(1) }
        }
        .padding(.horizontal, Theme.pad)
        .frame(height: Theme.navH)
        .opacity(store.articles.isEmpty ? 0.3 : 1)
        .disabled(store.articles.isEmpty)
    }

    private func navButton(_ icon: String, filled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: 10, weight: .heavy))
                .foregroundStyle(filled ? Theme.bg : Theme.dim)
                .frame(width: 24, height: 24)
                .background(
                    Circle().fill(filled ? Theme.accent : Theme.surface)
                )
        }
        .buttonStyle(.plain)
    }

    private var counter: String {
        guard !store.articles.isEmpty else { return "—" }
        return "\(current + 1) / \(store.articles.count)"
    }

    /// The article the top card is showing.
    private var current: Int {
        let n = store.articles.count
        guard n > 0 else { return 0 }
        return ((position % n) + n) % n
    }

    // MARK: - Empty and error states

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: store.fetchFailed ? "wifi.exclamationmark" : "newspaper")
                .font(.system(size: 20))
                .foregroundStyle(Theme.dim)
            Text(store.fetchFailed ? "Can't reach the backend" : "No stories yet")
                .font(.system(size: Theme.bodySize, weight: .semibold))
                .foregroundStyle(Theme.white)
            // The cause is almost always one of two things: the server is not
            // running, or it is on a different port than this expects. Say
            // both rather than "error".
            Text(store.lastError ?? "Start it with `uv run esp-serve --port 8010`")
                .font(.system(size: Theme.metaSize))
                .foregroundStyle(Theme.dim)
                .multilineTextAlignment(.center)
            Text(store.baseURL.absoluteString)
                .font(.system(size: Theme.metaSize, design: .monospaced))
                .foregroundStyle(Theme.dim.opacity(0.6))
                .lineLimit(1)
            Button("Retry") { Task { await store.load() } }
                .buttonStyle(.plain)
                .font(.system(size: Theme.metaSize, weight: .bold))
                .foregroundStyle(Theme.accent)
                .padding(.top, 2)
        }
        .padding(Theme.pad)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Navigation

    /// One card forward or back. Works the same whether a story is open or
    /// not: with the deck showing it flips the card, with a story open it
    /// walks to the next story and leaves the deck on the same one, so
    /// closing the story puts you where you actually are.
    private func step(_ delta: Int) {
        guard !store.articles.isEmpty else { return }
        audio.stop()
        withAnimation(DeckView.step) {
            position += delta
            if openIndex != nil { openIndex = current }
        }
    }

    private func open() {
        guard openIndex == nil, !store.articles.isEmpty else { return }
        withAnimation(DeckView.step) { openIndex = current }
    }

    private func close() {
        // Leaving a story stops its narration — otherwise audio keeps playing
        // over a deck you are already flipping through.
        audio.stop()
        withAnimation(DeckView.step) { openIndex = nil }
    }
}
