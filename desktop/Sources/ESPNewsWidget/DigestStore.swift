//
//  DigestStore.swift — what the panel knows, and when it goes and finds out.
//
//  Mirrors the firmware's news_client.c state machine: a poll loop that
//  re-reads the digest the backend has already written, plus an explicit
//  rebuild that asks the backend to re-run the pipeline.
//
//  The distinction matters and is easy to get wrong. Fetching RSS only
//  happens when the pipeline runs, so a plain re-GET of digest.json redraws
//  the same stories no matter how often you do it. The Refresh button is
//  therefore the BOOT button: POST /refresh, then wait out the ~20-30 s run.
//

import Foundation
import SwiftUI

@MainActor
final class DigestStore: ObservableObject {

    // MARK: Published state

    @Published private(set) var articles: [Article] = []
    @Published private(set) var generated: String?
    @Published private(set) var ageHours: Double?
    @Published private(set) var rebuilding = false
    @Published private(set) var fetchFailed = false
    @Published private(set) var lastError: String?
    @Published private(set) var hasLoaded = false

    // MARK: Configuration

    /// Same 15-minute cadence as NEWS_REFRESH_INTERVAL_MS in news_client.h.
    /// This only re-reads what the backend has written; it does not fetch RSS.
    private static let pollInterval: TimeInterval = 15 * 60

    /// How often to check whether a rebuild has finished, and how long to keep
    /// checking. A run is 20-30 s; three minutes is generous enough that
    /// giving up means something is actually wrong.
    private static let rebuildPollInterval: TimeInterval = 2
    private static let rebuildTimeout: TimeInterval = 180

    private(set) var client: NewsClient
    private var pollTask: Task<Void, Never>?

    init(baseURL: URL) {
        self.client = NewsClient(baseURL: baseURL)
    }

    func setBaseURL(_ url: URL) {
        client = NewsClient(baseURL: url)
        Task { await load() }
    }

    var baseURL: URL { client.baseURL }

    // MARK: - Status line
    //
    // Ported from render_status(). The order of these branches is the whole
    // point: rebuilding is checked before the article count, because a
    // rebuild runs with a full list still on screen and "10 stories" for
    // thirty seconds reads as a dead button.

    var statusText: String {
        if rebuilding      { return "refreshing…" }
        if !articles.isEmpty {
            return articles.count == 1 ? "1 story" : "\(articles.count) stories"
        }
        if fetchFailed     { return "fetch failed" }
        if !hasLoaded      { return "loading…" }
        return "no stories"
    }

    /// Freshness for the header, e.g. "2h ago". Nil until a digest is loaded.
    var ageText: String? {
        guard let ageHours else { return nil }
        if ageHours < 1 { return "\(max(Int(ageHours * 60), 1))m ago" }
        if ageHours < 24 { return "\(Int(ageHours))h ago" }
        return "\(Int(ageHours / 24))d ago"
    }

    // MARK: - Polling

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.load()
                try? await Task.sleep(for: .seconds(Self.pollInterval))
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: - Loading

    /// Re-read the digest the backend has already written.
    func load() async {
        do {
            async let digest = client.fetchDigest()
            async let health = try? client.fetchHealth()

            let loaded = try await digest
            // Replacing the array wholesale is fine — SwiftUI diffs on the
            // article ids, so unchanged cards are not rebuilt.
            articles    = loaded.articles
            generated   = loaded.generated
            ageHours    = await health?.ageHours
            lastError   = await health?.lastError
            fetchFailed = false
            hasLoaded   = true
        } catch {
            fetchFailed = true
            hasLoaded   = true
            lastError   = error.localizedDescription
        }
    }

    /// The BOOT button: re-run the pipeline, then reload when it lands.
    func rebuild() async {
        guard !rebuilding else { return }
        rebuilding = true
        lastError  = nil

        do {
            try await client.requestRebuild()
        } catch {
            lastError  = error.localizedDescription
            rebuilding = false
            return
        }

        let deadline = Date().addingTimeInterval(Self.rebuildTimeout)
        while Date() < deadline {
            try? await Task.sleep(for: .seconds(Self.rebuildPollInterval))
            guard let health = try? await client.fetchHealth() else { continue }
            if !health.refreshing {
                lastError = health.lastError
                rebuilding = false
                await load()
                return
            }
        }

        // Timed out. The run may still finish; the next poll will pick it up.
        rebuilding = false
        lastError  = "refresh timed out after \(Int(Self.rebuildTimeout))s"
    }
}
