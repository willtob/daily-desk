//
//  Snapshot.swift — render the widget to a PNG without running it.
//
//  Same reasoning as firmware/sim: looking at the UI should cost seconds, not
//  a launch-and-squint cycle, and a rendered file can be diffed and attached
//  to a commit. ImageRenderer draws the real views at the real panel size, so
//  what lands in the PNG is what the widget shows.
//
//      swift run ESPNewsWidget --snapshot out/            both views
//      swift run ESPNewsWidget --snapshot out/ --offline  use the fixture
//
//  Live data is used when the backend answers; otherwise it falls back to a
//  fixture so the snapshot works on a plane.
//
//  What a still cannot show is the flip, which is most of the design. The
//  deck is therefore rendered at three positions so the stack, the tints and
//  the wrap are all checkable; the motion still needs the running app.
//

import AppKit
import SwiftUI

@MainActor
enum Snapshot {

    /// The panel's default size. Snapshots are pinned to it rather than run
    /// to natural height: the deck is height-driven, and letting it grow
    /// would render a card no window will ever show.
    static let size = CGSize(width: 288, height: 300)

    /// Renders the deck and a story into `directory`. False if writing failed.
    static func run(directory: String, offline: Bool, baseURL: URL) -> Bool {
        let dir = URL(fileURLWithPath: directory, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let articles = offline ? fixture : (fetchSync(baseURL: baseURL) ?? fixture)
        guard let first = articles.first else {
            FileHandle.standardError.write(Data("snapshot: no articles\n".utf8))
            return false
        }

        let client = NewsClient(baseURL: baseURL)
        var ok = true

        // Three positions: the tints and the peek stack change under you as
        // the deck advances, and only the first card is checkable from one.
        for position in 0..<3 {
            let view = shell(position: position, count: articles.count) {
                DeckView(articles: articles, position: position, onOpen: { _ in })
                    .padding(.horizontal, Theme.pad)
                    .padding(.bottom, 2)
            }
            ok = write(view, to: dir.appendingPathComponent("deck-\(position).png")) && ok
        }

        // Natural height, unlike the deck: a story is taller than the panel
        // and scrolls in the app, so pinning it to 300 px would render a
        // headline with its top row cropped off rather than a story.
        let detail = shellRaw(fixedHeight: false) {
            DetailView(article: first, index: 0, client: client,
                       onBack: {}, audio: AudioPlayer(), scrollable: false)
        }
        ok = write(detail, to: dir.appendingPathComponent("detail.png")) && ok

        return ok
    }

    /// The widget's own chrome around a deck: header, then content, then the
    /// nav bar. Duplicated from RootView rather than driven through it
    /// because RootView owns a polling store and a panel controller, neither
    /// of which should be spun up to draw a picture.
    private static func shell<Content: View>(
        position: Int,
        count: Int,
        @ViewBuilder _ content: () -> Content
    ) -> some View {
        shellRaw {
            VStack(spacing: 0) {
                HStack(spacing: 6) {
                    Text("NEWS")
                        .font(.system(size: Theme.metaSize + 1, weight: .black))
                        .tracking(2)
                        .foregroundStyle(Theme.accent)
                    Spacer()
                    Text("2h ago")
                        .font(.system(size: Theme.metaSize))
                        .foregroundStyle(Theme.dim)
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(Theme.dim)
                }
                .padding(.horizontal, Theme.pad)
                .frame(height: Theme.headerH)

                content()

                HStack(spacing: 8) {
                    nav("chevron.left", filled: false)
                    Spacer()
                    Text("\(position % max(count, 1) + 1) / \(count)")
                        .font(.system(size: Theme.metaSize, weight: .medium, design: .monospaced))
                        .foregroundStyle(Theme.dim)
                    Spacer()
                    nav("chevron.right", filled: true)
                }
                .padding(.horizontal, Theme.pad)
                .frame(height: Theme.navH)
            }
        }
    }

    private static func nav(_ icon: String, filled: Bool) -> some View {
        Image(systemName: icon)
            .font(.system(size: 10, weight: .heavy))
            .foregroundStyle(filled ? Theme.bg : Theme.dim)
            .frame(width: 24, height: 24)
            .background(Circle().fill(filled ? Theme.accent : Theme.surface))
    }

    /// Panel-sized, rounded, on the wallpaper's mid-orange so the snapshot
    /// shows the widget against something like the desktop it will sit on
    /// rather than floating on white.
    private static func shellRaw<Content: View>(
        fixedHeight: Bool = true,
        @ViewBuilder _ content: () -> Content
    ) -> some View {
        content()
            .frame(width: size.width, height: fixedHeight ? size.height : nil, alignment: .top)
            .background(Theme.bg)
            .clipShape(RoundedRectangle(cornerRadius: Theme.shellRadius, style: .continuous))
            .padding(16)
            .background(Color(hex: 0xE48142))
    }

    private static func write(_ view: some View, to url: URL) -> Bool {
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2                       // retina, so text is judgeable

        guard
            let image = renderer.nsImage,
            let tiff = image.tiffRepresentation,
            let rep = NSBitmapImageRep(data: tiff),
            let png = rep.representation(using: .png, properties: [:])
        else {
            FileHandle.standardError.write(Data("snapshot: render failed\n".utf8))
            return false
        }

        do {
            try png.write(to: url)
            print("wrote \(url.path)")
            return true
        } catch {
            FileHandle.standardError.write(Data("snapshot: \(error)\n".utf8))
            return false
        }
    }

    /// Blocking fetch. Fine here and nowhere else: this path runs before the
    /// app loop starts and then exits.
    private static func fetchSync(baseURL: URL) -> [Article]? {
        let client = NewsClient(baseURL: baseURL)
        let sem = DispatchSemaphore(value: 0)
        nonisolated(unsafe) var result: [Article]?

        Task.detached {
            result = try? await client.fetchDigest().articles
            sem.signal()
        }
        _ = sem.wait(timeout: .now() + 10)
        return (result?.isEmpty == false) ? result : nil
    }

    /// Four stories chosen to exercise the edges: a headline long enough to
    /// hit the three-line clamp, one short enough to leave the card airy, an
    /// area at the badge width limit, and a low score so the bar has to read
    /// as low rather than as missing.
    private static let fixture: [Article] = [
        Article(
            title: "Open-Weights Mythos Capabilities Are Coming. We're Not Ready.",
            summary: """
                The post argues there is an 85% chance that within 24 months an \
                open-weights model or system will reach “Mythos”-level cybersecurity \
                capability, and that society is not ready for the consequences. It says \
                banning open-weights releases worldwide is unlikely, and that closed-weights \
                labs would still need unusually strong cybersecurity to avoid weight \
                exfiltration.
                """,
            source: "LessWrong",
            matchedArea: "ai_open_source",
            score: 0.6373,
            url: "https://example.com/a"
        ),
        Article(
            title: "A shorter headline",
            summary: "Body copy for the second card, long enough to wrap onto a second line and be clamped.",
            source: "Hacker News Front Page",
            matchedArea: "embedded_wearables",
            score: 0.4834,
            url: "https://example.com/b"
        ),
        Article(
            title: "Spain's grid operator says the April blackout was a control problem",
            summary: "Red Eléctrica's post-mortem points at voltage control rather than generation mix.",
            source: "El País",
            matchedArea: "spain",
            score: 0.3120,
            url: "https://example.com/c"
        ),
        Article(
            title: "Gradient boosting still wins on tabular data",
            summary: "A survey across 45 datasets finds trees ahead of transformers where rows beat pixels.",
            source: "arXiv",
            matchedArea: "classic_ml_applied",
            score: 0.2680,
            url: "https://example.com/d"
        ),
    ]
}
