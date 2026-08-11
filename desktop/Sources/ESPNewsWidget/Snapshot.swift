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

        // A live badge, not the inert one. The pull is the part of this UI most
        // worth looking at without launching anything: set ESP_NEWS_SLIME to
        // freeze every badge mid-pull and the blob renders into these files at
        // whatever travel is being tuned. The seeded verdicts are so the marks
        // that a pull leaves behind are on screen too.
        let feedback = FeedbackStore()
        feedback.seed(Dictionary(uniqueKeysWithValues: zip(
            articles.prefix(2).map(\.url), [FeedbackStore.Verdict.like, .dislike]
        )))

        // Three positions: the tints and the peek stack change under you as
        // the deck advances, and only the first card is checkable from one.
        for position in 0..<3 {
            let view = shell(position: position, count: articles.count) {
                DeckView(articles: articles, position: position,
                         feedback: feedback, client: client, onOpen: { _ in })
                    .padding(.horizontal, Theme.pad)
                    .padding(.bottom, 2)
            }
            ok = write(view, to: dir.appendingPathComponent("deck-\(position).png")) && ok
        }

        // Natural height, unlike the deck: a story is taller than the panel
        // and scrolls in the app, so pinning it to 300 px would render a
        // headline with its top row cropped off rather than a story.
        //
        // Two stories, not one. The first exercises bold and paragraphs; the
        // second exercises bullets, which are laid out as rows rather than as
        // text and are the part most likely to break silently.
        for (n, article) in articles.prefix(2).enumerated() {
            let detail = shellRaw(fixedHeight: false) {
                DetailView(article: article, index: n, client: client,
                           onBack: {}, audio: AudioPlayer(),
                           feedback: feedback, panel: PanelController(),
                           scrollable: false)
            }
            let name = n == 0 ? "detail.png" : "detail-\(n).png"
            ok = write(detail, to: dir.appendingPathComponent(name)) && ok
        }
        _ = first

        ok = learnScreens(into: dir) && ok

        return ok
    }

    // MARK: - The learning tab
    //
    // Each screen is only reachable by living through a fifteen-minute
    // session, which is precisely why stills of them are worth having. The
    // store is posed rather than driven — see LearnStore.posed.

    private static func learnScreens(into dir: URL) -> Bool {
        let topic = LearnTopic(topicID: "kv_cache",
                               name: "The KV cache",
                               difficulty: "advanced")

        let grade = Grade(
            score: 6,
            feedback: "The mechanism is right and the memory cost is missing, which is "
                    + "the half that decides whether you would reach for it. Start with "
                    + "what the cache actually holds per token, per layer.",
            missedConcepts: [
                "that cache memory grows linearly with context and batch size",
                "why it is what limits concurrent requests on a GPU",
            ],
            strengths: [
                "correctly said decoding reuses past keys and values",
                "named the quadratic cost of recomputing the prefix",
            ],
            counted: false,
            passScore: 7
        )

        let stats = LearnStats(
            currentStreak: 4, longestStreak: 9, sessionsCompleted: 23,
            rollingAverage: 7.4, averageAllTime: 6.8, rollingWindow: 10,
            passScore: 7, lastSessionAt: nil
        )

        let explanation = "The KV cache stores the key and value vectors for every token "
                        + "already in the context, so generating the next token only "
                        + "computes attention for that one new position instead of "
                        + "redoing the whole prefix."

        // remaining is set to something mid-run so the border trace is
        // partially spent — a full or empty one proves nothing about it.
        let screens: [(String, LearnStore)] = [
            // The border trace at four points in a run. Kept as real output
            // rather than deleted after the fact: where a RoundedRectangle's
            // path starts is undocumented, TimerBorder's 0.75 offset depends
            // on it, and a regression there is a quiet cosmetic wrong rather
            // than a crash. These four frames are how it was pinned down and
            // are the cheapest way to see it break.
            ("timer-sweep-90", .posed(phase: .timer, topic: topic, remaining: 0.90 * 900)),
            ("timer-sweep-60", .posed(phase: .timer, topic: topic, remaining: 0.60 * 900)),
            ("timer-sweep-30", .posed(phase: .timer, topic: topic, remaining: 0.30 * 900)),
            ("timer-sweep-05", .posed(phase: .timer, topic: topic, remaining: 0.05 * 900)),
            ("learn-topic",   .posed(phase: .topic, topic: topic)),
            ("learn-timer",   .posed(phase: .timer, topic: topic, remaining: 9 * 60 + 12)),
            ("learn-paused",  .posed(phase: .timer, topic: topic, remaining: 4 * 60 + 5,
                                     paused: true)),
            ("learn-explain", .posed(phase: .explaining, topic: topic,
                                     explanation: explanation)),
            ("learn-grading", .posed(phase: .grading, topic: topic)),
            ("learn-result",  .posed(phase: .result, topic: topic, grade: grade)),
            ("learn-stats",   .posed(phase: .stats, topic: topic, stats: stats)),
        ]

        var ok = true
        for (name, store) in screens {
            ok = write(learnShell(store), to: dir.appendingPathComponent("\(name).png")) && ok
        }
        return ok
    }

    /// RootView's chrome for the learning tab: the switcher, the view, and the
    /// border trace when a session is running. Duplicated from RootView for the
    /// same reason `shell` is — RootView owns a polling store and a panel.
    private static func learnShell(_ store: LearnStore) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 5) {
                tabs(active: .learn)
                Spacer()
                if let streak = store.stats?.currentStreak, streak > 0 {
                    Text("\(streak) days")
                        .font(.system(size: Theme.metaSize))
                        .foregroundStyle(Theme.dim)
                }
            }
            .padding(.horizontal, Theme.pad)
            .frame(height: Theme.headerH)

            LearnView(store: store, scrolls: false)
        }
        .frame(width: size.width, height: size.height, alignment: .top)
        .background(Theme.bg)
        .clipShape(RoundedRectangle(cornerRadius: Theme.shellRadius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.shellRadius, style: .continuous)
                .stroke(Color.white.opacity(0.07), lineWidth: 1)
        )
        .overlay {
            if store.phase == .timer {
                TimerBorder(elapsed: store.elapsedFraction)
            }
        }
        .padding(16)
        .background(Color(hex: 0xE48142))
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
                    tabs(active: .news)
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

    /// RootView's header switcher. Both shells draw it so a snapshot cannot
    /// quietly show chrome the app no longer has.
    private static func tabs(active: Tab) -> some View {
        HStack(spacing: 5) {
            ForEach(Tab.allCases, id: \.self) { item in
                Text(item.title)
                    .font(.system(size: Theme.metaSize + 1, weight: .black))
                    .tracking(2)
                    .foregroundStyle(item == active ? Theme.accent : Theme.dim.opacity(0.55))
                if item != Tab.allCases.last {
                    Text("·")
                        .font(.system(size: Theme.metaSize + 1, weight: .black))
                        .foregroundStyle(Theme.dim.opacity(0.35))
                }
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
    ///
    /// The first two also carry the Markdown subset the summarizer is allowed
    /// to emit — bold, bullets and blank-line paragraphs — because the live
    /// backend serves whatever is in its cache, and a summary written under an
    /// older prompt has none of it. Without a fixture that does, the renderer
    /// looks correct right up until the first digest that uses it. The third
    /// is deliberately left as one unbroken block, which is what everything
    /// cached under PROMPT_VERSION 1 looks like, so the fallback pacing is
    /// exercised too.
    private static let fixture: [Article] = [
        Article(
            title: "Open-Weights Mythos Capabilities Are Coming. We're Not Ready.",
            summary: """
                The post argues there is an **85% chance** that within **24 months** an \
                open-weights model or system will reach “Mythos”-level cybersecurity \
                capability, and that society is not ready for the consequences.

                It says banning open-weights releases worldwide is unlikely, and that \
                closed-weights labs would still need unusually strong cybersecurity to \
                avoid weight exfiltration. Arithmetic like 5 x 3 and identifiers like \
                snake_case_names must survive the parser untouched.
                """,
            source: "LessWrong",
            matchedArea: "ai_open_source",
            score: 0.6373,
            url: "https://example.com/a"
        ),
        Article(
            title: "A shorter headline",
            summary: """
                The filing sets out three changes, and the card excerpt has to \
                flatten all of this back to plain prose:

                - A **$21.7M** round led by Khosla Ventures
                - Two board seats, one of them independent
                - A commitment to publish the eval harness
                """,
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
            matchedArea: "model_architectures",
            score: 0.2680,
            url: "https://example.com/d"
        ),
        Article(
            title: "Los 23 mejores arroces de Barcelona",
            summary: "Paellas, arroces melosos y fideuàs: los clásicos del Poblenou y la Barceloneta, y algunas aperturas recientes del Eixample.",
            source: "Time Out Barcelona",
            matchedArea: "barcelona_dates",
            score: 0.6640,
            url: "https://example.com/e"
        ),
        // Last, and deliberately carrying an area that is already in the deck:
        // the badge has to come from the wildcard flag, not from the area being
        // unrecognised. Same case the firmware simulator covers.
        Article(
            title: "A 1970s synthesiser restored with a logic analyser and a lot of patience",
            summary: "The exploration slot: drawn at random from the middle of the ranking on purpose, so the digest carries one thing the profile did not ask for.",
            source: "Hackaday",
            matchedArea: "embedded_wearables",
            // Mid-pack, matching the 40th-70th percentile band the backend now
            // draws from. At the old 0.1661 the score bar was a bare sliver.
            score: 0.4044,
            url: "https://example.com/f",
            wildcard: true
        ),
    ]
}
