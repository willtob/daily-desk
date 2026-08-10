//
//  NewsClient.swift — the same HTTP contract the firmware speaks.
//
//  This client talks to the FastAPI app in src/esp_news/api.py and nothing
//  else. It deliberately does not import or re-run the pipeline: a full run
//  takes 20-30 s, and the split between "serve the last digest" and "kick off
//  a rebuild" is the whole reason the backend is shaped the way it is.
//
//      GET  /digest.json     the digest, already curated and summarized
//      GET  /health          freshness, and whether a rebuild is running
//      POST /refresh         start a pipeline run, returns 202 immediately
//      GET  /audio/{i}.pcm   raw PCM narration of article i
//      GET  /feedback        every like/dislike currently recorded
//      POST /feedback        record a verdict, or clear one
//
//  The feedback half is specified in docs/feedback-api.md, which is the
//  contract rather than a description of it. Two things there are load-bearing
//  here: the endpoints are idempotent, so a retry after a timeout is safe; and
//  clearing something unrated returns 200, so an undo fired twice is not an
//  error to show anybody.
//

import Foundation

// MARK: - Wire types

/// One article, exactly as `digest_payload()` writes it.
struct Article: Decodable, Identifiable, Equatable {
    let title: String
    let summary: String
    let source: String
    let matchedArea: String?
    let score: Double
    let url: String
    /// The backend's exploration slot: one article appended to the digest
    /// *because* it scored badly. Optional and defaulted, so a digest written
    /// before this field existed still decodes.
    var wildcard: Bool? = nil

    /// The payload has no id of its own; the canonical URL is what the seen
    /// store keys on, so it is the natural identity here too.
    var id: String { url }

    enum CodingKeys: String, CodingKey {
        case title, summary, source, score, url, wildcard
        case matchedArea = "matched_area"
    }
}

struct Digest: Decodable {
    let generated: String
    let count: Int
    let articles: [Article]
}

/// One recorded verdict, as `GET /feedback` lists them.
///
/// Only the two fields this app acts on are decoded. The record also carries
/// the title, area, score and the embedded text, all of which the backend needs
/// and none of which a thumb on a card does.
struct VerdictRecord: Decodable {
    let url: String
    let verdict: String
}

struct VerdictList: Decodable {
    let count: Int
    let verdicts: [VerdictRecord]
}

struct Health: Decodable {
    let status: String
    let generated: String?
    let ageHours: Double?
    let refreshing: Bool
    let lastError: String?

    enum CodingKeys: String, CodingKey {
        case status, generated, refreshing
        case ageHours  = "age_hours"
        case lastError = "last_error"
    }
}

/// How to interpret the bytes from /audio/{i}.pcm.
struct PCMFormat {
    var sampleRate: UInt32
    var bits: UInt16
    var channels: UInt16

    /// What tts.py currently synthesizes at, for a server too old to send the
    /// headers. Matches NEWS_AUDIO_SAMPLE_RATE in the firmware.
    static let fallback = PCMFormat(sampleRate: 16_000, bits: 16, channels: 1)

    init(sampleRate: UInt32, bits: UInt16, channels: UInt16) {
        self.sampleRate = sampleRate
        self.bits = bits
        self.channels = channels
    }

    init(headers response: HTTPURLResponse) {
        func value<T: FixedWidthInteger>(_ name: String, _ fallback: T) -> T {
            guard
                let raw = response.value(forHTTPHeaderField: name),
                let parsed = T(raw.trimmingCharacters(in: .whitespaces)),
                parsed > 0
            else { return fallback }
            return parsed
        }
        self.sampleRate = value("X-Sample-Rate",      PCMFormat.fallback.sampleRate)
        self.bits       = value("X-Bits-Per-Sample",  PCMFormat.fallback.bits)
        self.channels   = value("X-Channels",         PCMFormat.fallback.channels)
    }
}

// MARK: - Errors

enum NewsClientError: LocalizedError {
    case badURL
    case http(Int, String)
    case noDigestYet

    var errorDescription: String? {
        switch self {
        case .badURL:
            return "bad base URL"
        case .noDigestYet:
            // 503 from the backend means the pipeline has never written a
            // digest, which is a setup state rather than a failure.
            return "no digest yet — run `uv run esp-digest`"
        case let .http(code, detail):
            return detail.isEmpty ? "HTTP \(code)" : "HTTP \(code): \(detail)"
        }
    }
}

// MARK: - Client

struct NewsClient {

    let baseURL: URL

    /// Matches the firmware's HTTP timeout. Every one of these endpoints
    /// reads a file the backend has already written, so anything slower than
    /// this means the backend is wedged, not busy.
    private static let timeout: TimeInterval = 8

    private var session: URLSession {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = Self.timeout
        // The digest changes under a fixed URL, so a cached 200 is exactly
        // the wrong answer after a rebuild.
        cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: cfg)
    }

    // MARK: Requests

    func fetchDigest(limit: Int = 12) async throws -> Digest {
        var comps = URLComponents(url: baseURL.appendingPathComponent("digest.json"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        guard let url = comps?.url else { throw NewsClientError.badURL }

        let data = try await get(url)
        return try JSONDecoder().decode(Digest.self, from: data)
    }

    func fetchHealth() async throws -> Health {
        let data = try await get(baseURL.appendingPathComponent("health"))
        return try JSONDecoder().decode(Health.self, from: data)
    }

    /// Ask the backend to re-run the whole graph. Returns as soon as the run
    /// is accepted — the caller polls /health to see it finish.
    func requestRebuild() async throws {
        var req = URLRequest(url: baseURL.appendingPathComponent("refresh"))
        req.httpMethod = "POST"
        _ = try await send(req)
    }

    // MARK: Feedback

    /// Every recorded verdict, as `url -> "like" | "dislike"`.
    ///
    /// The stored article text is deliberately not requested (`include_text`
    /// defaults to false server-side): it runs to a thousand characters a
    /// record and nothing on this side reads it.
    func fetchVerdicts() async throws -> [String: String] {
        let data = try await get(baseURL.appendingPathComponent("feedback"))
        let list = try JSONDecoder().decode(VerdictList.self, from: data)
        return Dictionary(
            list.verdicts.map { ($0.url, $0.verdict) },
            uniquingKeysWith: { _, latest in latest }
        )
    }

    /// Record `like` or `dislike` for an article. Idempotent — sending the same
    /// verdict twice leaves one verdict, so a retry is always safe.
    func setVerdict(url: String, verdict: String) async throws {
        try await postFeedback(url: url, verdict: verdict)
    }

    /// Remove whatever verdict an article has.
    ///
    /// `POST … "clear"` rather than `DELETE`, though the backend accepts both.
    /// One method means one code path for the optimistic update to fail down,
    /// and the firmware — which may get this gesture later — only speaks POST.
    func clearVerdict(url: String) async throws {
        try await postFeedback(url: url, verdict: "clear")
    }

    private func postFeedback(url: String, verdict: String) async throws {
        var req = URLRequest(url: baseURL.appendingPathComponent("feedback"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(
            withJSONObject: ["url": url, "verdict": verdict]
        )
        _ = try await send(req)
    }

    /// Raw PCM narration, plus the format it is in.
    ///
    /// The format is read from the X-Sample-Rate / X-Bits-Per-Sample /
    /// X-Channels headers rather than hardcoded. Those headers exist for the
    /// firmware's benefit and they are the only authority on the matter —
    /// tts.py has changed rate before, and the docstring in api.py is
    /// currently out of date about it. Guessing here buys a narration that
    /// plays at the wrong speed and sounds like a fault in the synthesis.
    func fetchAudio(index: Int) async throws -> (Data, PCMFormat) {
        let url = baseURL.appendingPathComponent("audio/\(index).pcm")
        let (data, response) = try await session.data(from: url)

        guard let http = response as? HTTPURLResponse else {
            return (data, .fallback)
        }
        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 503 { throw NewsClientError.noDigestYet }
            throw NewsClientError.http(http.statusCode, Self.detail(from: data))
        }
        return (data, PCMFormat(headers: http))
    }

    // MARK: Plumbing

    private func get(_ url: URL) async throws -> Data {
        try await send(URLRequest(url: url))
    }

    private func send(_ req: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { return data }

        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 503 { throw NewsClientError.noDigestYet }
            throw NewsClientError.http(http.statusCode, Self.detail(from: data))
        }
        return data
    }

    /// FastAPI puts the useful part of an error in `{"detail": ...}`; surface
    /// that rather than a bare status code.
    private static func detail(from data: Data) -> String {
        guard
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let detail = obj["detail"] as? String
        else { return "" }
        return detail
    }
}
