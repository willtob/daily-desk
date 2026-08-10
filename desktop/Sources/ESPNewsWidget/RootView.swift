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
    @StateObject private var pager = TrackpadPager()
    @StateObject private var feedback = FeedbackStore()

    /// Monotonic deck position — see DeckView. Not an index into `articles`.
    @State private var position = 0
    /// Which story is open over the deck, if any.
    @State private var openIndex: Int?

    /// Where the top card is, so a story can grow out of it. Zero until the
    /// deck has laid out — see CardFrameKey.
    @State private var cardRect: CGRect = .zero
    /// Whether the open story has finished growing to fill the panel.
    @State private var expanded = false
    /// Cursor presence, for the idle fade.
    @State private var hovering = false

    /// Opening is the moment worth dwelling on; leaving is not. Both are
    /// damped close to critical — the deck flip gets a bounce because a card
    /// landing should, but a whole screen that overshoots reads as a glitch.
    private static let expandAnim   = Animation.spring(response: 0.40, dampingFraction: 0.86)
    private static let collapseAnim = Animation.spring(response: 0.28, dampingFraction: 0.92)

    /// How far the panel fades when the pointer is elsewhere. Enough to sink
    /// into the wallpaper, not so far that the headline stops being readable —
    /// the whole point is to be glanceable without being asked.
    private static let idleOpacity = 0.55

    var body: some View {
        GeometryReader { geo in
            ZStack {
                Theme.bg

                VStack(spacing: 0) {
                    header
                    deck
                    nav
                }

                if let openIndex, openIndex < store.articles.count {
                    story(at: openIndex, panel: geo.size)
                }
            }
            .coordinateSpace(name: DeckView.space)
            .onPreferenceChange(CardFrameKey.self) { cardRect = $0 }
        }
        // Sink into the wallpaper when nobody is looking. Only on the desktop:
        // floating placement is an explicit "show me this now", and fading
        // something the user just asked to see would be perverse.
        .opacity(dimmed ? Self.idleOpacity : 1)
        .animation(.easeOut(duration: 0.25), value: dimmed)
        .onHover { hovering = $0 }
        // The window itself is transparent; this is the widget's real edge.
        .clipShape(RoundedRectangle(cornerRadius: Theme.shellRadius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.shellRadius, style: .continuous)
                .stroke(Color.white.opacity(0.07), lineWidth: 1)
        )
        .task { store.startPolling() }
        // Verdicts recorded on another client — or in an earlier session —
        // belong on the cards as soon as they are drawn.
        .task { await feedback.load(using: store.client) }
        // A poll can replace the deck wholesale; re-reading is cheap and keeps
        // a story that gained a verdict elsewhere from showing up unrated.
        .onChange(of: store.articles) {
            Task { await feedback.load(using: store.client) }
        }
        // Two fingers sideways flips the deck, and walks stories when one is
        // open — the same thing the arrow keys and the nav buttons do, so
        // there is one idea of what "next" means rather than three.
        .onAppear {
            pager.trace = ProcessInfo.processInfo.environment["ESP_NEWS_TRACE_SCROLL"] != nil
            pager.onPage = { step($0) }
            pager.start()
        }
        .onDisappear { pager.stop() }
        .onKeyPress(.escape) {
            guard openIndex != nil else { return .ignored }
            close()
            return .handled
        }
        .onKeyPress(.leftArrow)  { step(-1); return .handled }
        .onKeyPress(.rightArrow) { step(1);  return .handled }
        .onKeyPress(.space)      { open();   return .handled }
    }

    private var dimmed: Bool {
        controller.placement == .desktop && !hovering && openIndex == nil
    }

    // MARK: - The story, grown out of its card
    //
    // The card does not slide aside and the story does not arrive from an
    // edge: the story *is* the card, opened out. It starts at the card's exact
    // rect, in the card's own tint and corner radius, and grows to fill the
    // panel. For the first frame the two are indistinguishable, which is what
    // makes it read as opening the thing you touched rather than as arriving
    // somewhere else.
    //
    // The two frames are the whole trick, and the order matters. The inner one
    // lays the story out at the *panel's* size, so its text is wrapped for
    // where it is going; the outer one is the window that grows, anchored top
    // left, with everything past it clipped away. Laying the story out at the
    // intermediate size instead would re-wrap every line on every frame, and
    // the headline would visibly reflow all the way open.
    //
    // This mirrors the firmware, where it falls out of LVGL clipping children
    // to their parent's rectangle; here it has to be asked for.

    @ViewBuilder
    private func story(at index: Int, panel: CGSize) -> some View {
        let full = CGRect(origin: .zero, size: panel)
        // No card to grow from — an empty deck, or a story opened before the
        // deck has laid out. Opening at full size is right; growing out of the
        // top-left corner would not be.
        let target = (expanded || cardRect == .zero) ? full : cardRect

        DetailView(
            article: store.articles[index],
            index: index,
            client: store.client,
            onBack: close,
            audio: audio,
            feedback: feedback,
            panel: controller
        )
        .frame(width: panel.width, height: panel.height, alignment: .topLeading)
        .frame(width: target.width, height: target.height, alignment: .topLeading)
        .clipShape(
            RoundedRectangle(
                cornerRadius: expanded ? Theme.shellRadius : Theme.cardRadius,
                style: .continuous
            )
        )
        .position(x: target.midX, y: target.midY)
        .zIndex(2)
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
        // The header is the title bar. A borderless window has none, so this
        // strip is it: the one place a drag moves the panel rather than doing
        // something to a story. The refresh button inside it still takes its
        // own clicks — a Button's gesture outranks one on an ancestor.
        .gesture(controller.dragGesture())
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
        Toggle("Open at Login", isOn: Binding(
            get: { LoginItem.isEnabled },
            set: { LoginItem.set($0) }
        ))
        .disabled(!LoginItem.available)
        Divider()
        Button("Quit") { NSApplication.shared.terminate(nil) }
    }

    // MARK: - Deck

    @ViewBuilder
    private var deck: some View {
        if store.articles.isEmpty {
            emptyState
        } else {
            DeckView(articles: store.articles,
                     position: position,
                     feedback: feedback,
                     client: store.client) { index in
                open(index)
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

    private func open() { open(current) }

    private func open(_ index: Int) {
        guard openIndex == nil, !store.articles.isEmpty else { return }
        // Placed on the card first, then grown, in two steps: SwiftUI has to
        // render the collapsed state once for there to be anything to animate
        // from. Setting both in the same pass just appears at full size.
        expanded = false
        openIndex = index
        DispatchQueue.main.async {
            withAnimation(Self.expandAnim) { expanded = true }
        }
    }

    private func close() {
        // Leaving a story stops its narration — otherwise audio keeps playing
        // over a deck you are already flipping through.
        audio.stop()
        withAnimation(Self.collapseAnim) { expanded = false }

        // The view can only be removed once it has finished shrinking, and
        // macOS 14 has no completion handler for withAnimation. The guard is
        // the point: reopening during the collapse leaves `expanded` true, and
        // without it this would fire anyway and close the story the reader has
        // just opened.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.32) {
            if !expanded { openIndex = nil }
        }
    }
}
