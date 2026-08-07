//
//  Snapshot.swift — render the panel to a PNG without running it.
//
//  Same reasoning as firmware/sim: looking at the UI should cost seconds, not
//  a launch-and-squint cycle, and a rendered file can be diffed and attached
//  to a commit. ImageRenderer draws the real views at the real panel size, so
//  what lands in the PNG is what the panel shows.
//
//      swift run ESPNewsWidget --snapshot out/          both views
//      swift run ESPNewsWidget --snapshot out/ --offline  use the fixture
//
//  Live data is used when the backend answers; otherwise it falls back to a
//  fixture so the snapshot works on a plane.
//

import AppKit
import SwiftUI

@MainActor
enum Snapshot {

    static let size = CGSize(width: 340, height: 700)

    /// Renders list and detail into `directory`. Returns false if writing failed.
    static func run(directory: String, offline: Bool, baseURL: URL) -> Bool {
        let dir = URL(fileURLWithPath: directory, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let articles = offline ? fixture : (fetchSync(baseURL: baseURL) ?? fixture)
        guard let first = articles.first else {
            FileHandle.standardError.write(Data("snapshot: no articles\n".utf8))
            return false
        }

        let client = NewsClient(baseURL: baseURL)

        let list = panel {
            ListView(articles: articles, onSelect: { _ in }, scrollable: false)
        }
        let detail = panel {
            DetailView(article: first, index: 0, client: client,
                       onBack: {}, audio: AudioPlayer(), scrollable: false)
        }

        return write(list,   to: dir.appendingPathComponent("list.png"))
            && write(detail, to: dir.appendingPathComponent("detail.png"))
    }

    /// Wraps a view in the panel's own chrome so the snapshot includes the
    /// header — the status line is exactly the part that is easy to get
    /// wrong and impossible to check from the code.
    private static func panel<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(spacing: 0) {
            HStack {
                Text("NEWS")
                    .font(.system(size: Theme.metaSize + 1, weight: .bold))
                    .tracking(2)
                    .foregroundStyle(Theme.accent)
                Spacer()
                Text("10 stories")
                    .font(.system(size: Theme.metaSize))
                    .foregroundStyle(Theme.dim)
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.white)
            }
            .padding(.horizontal, Theme.pad)
            .frame(height: Theme.headerH)

            Divider().overlay(Theme.rule)
            content()
        }
        // Width is the panel's; height is whatever the content needs. Pinning
        // the height here would make an over-long list overflow and centre,
        // which silently crops the header off the top of the PNG. Letting the
        // image run tall also means a snapshot shows every card rather than
        // just the ones above the fold.
        .frame(width: size.width)
        .background(Theme.bg)
        .foregroundStyle(Theme.white)
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

    /// Two stories chosen to exercise the layout's edges: a long headline
    /// that has to wrap, and an area whose badge is at the width limit.
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
            summary: "Body copy for the second card.",
            source: "Hacker News Front Page",
            matchedArea: "embedded_wearables",
            score: 0.4834,
            url: "https://example.com/b"
        ),
    ]
}
