//
//  LearnClient.swift — the /learn half of the same FastAPI app.
//
//  Talks to src/esp_news/learn/api.py and nothing else, over the transport
//  NewsClient uses. Same shape as that client on purpose: wire types that
//  match the Python models field for field, async throws, and no state.
//
//      GET  /learn/topic          draw a topic to explain
//      POST /learn/session/start  open a session
//      POST /learn/grade          submit an explanation, get a structured grade
//      GET  /learn/stats          streak, sessions, rolling average
//
//  The `covers` hint is deliberately absent from LearnTopic, because it is
//  absent from the response. It is the grading checklist and the server never
//  sends it before an explanation — see the note on TopicOut in the backend.
//  If it ever appears here, something has gone wrong on the other side.
//

import Foundation

// MARK: - Wire types

/// A drawn topic. Three fields, and there is no fourth — see above.
///
/// Codable, not just Decodable: LearnStore persists a crash-recovery draft
/// with one of these in it, so it has to round-trip, not just arrive.
struct LearnTopic: Codable, Equatable {
    let topicID: String
    let name: String
    let difficulty: String

    enum CodingKeys: String, CodingKey {
        case name, difficulty
        case topicID = "topic_id"
    }
}

/// Codable for the same reason LearnTopic is — see there.
struct LearnSession: Codable, Equatable {
    let sessionID: String
    let topicID: String

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case topicID   = "topic_id"
    }

    // started_at is on the wire and deliberately not decoded. The countdown
    // runs off the local clock from the moment the call returns, so pulling in
    // the server's timestamp would only add clock skew between two machines to
    // a number the user is watching tick.
}

/// The grade, exactly as GradeOut writes it.
struct Grade: Decodable, Equatable {
    let score: Int
    let feedback: String
    let missedConcepts: [String]
    let strengths: [String]
    let counted: Bool
    let passScore: Int

    enum CodingKeys: String, CodingKey {
        case score, feedback, strengths, counted
        case missedConcepts = "missed_concepts"
        case passScore      = "pass_score"
    }
}

struct LearnStats: Decodable, Equatable {
    let currentStreak: Int
    let longestStreak: Int
    let sessionsCompleted: Int
    let rollingAverage: Double?
    let averageAllTime: Double?
    let rollingWindow: Int
    let passScore: Int
    /// Left as the raw string rather than decoded to a Date. FastAPI serialises
    /// datetimes with microseconds, which JSONDecoder's .iso8601 strategy
    /// rejects — and a strict date strategy that throws would take down the
    /// whole stats screen over a field used for one line of grey text.
    /// LearnStats.lastSessionText parses it leniently and gives up quietly.
    let lastSessionAt: String?

    enum CodingKeys: String, CodingKey {
        case currentStreak     = "current_streak"
        case longestStreak     = "longest_streak"
        case sessionsCompleted = "sessions_completed"
        case rollingAverage    = "rolling_average"
        case averageAllTime    = "average_all_time"
        case rollingWindow     = "rolling_window"
        case passScore         = "pass_score"
        case lastSessionAt     = "last_session_at"
    }

    /// "2h ago" for the header line, or nil if there is nothing to say.
    var lastSessionText: String? {
        guard let lastSessionAt, let when = Self.parse(lastSessionAt) else { return nil }
        let seconds = Date().timeIntervalSince(when)
        if seconds < 60      { return "just now" }
        if seconds < 3600    { return "\(Int(seconds / 60))m ago" }
        if seconds < 86_400  { return "\(Int(seconds / 3600))h ago" }
        return "\(Int(seconds / 86_400))d ago"
    }

    /// Tolerates fractional seconds, which pydantic emits and the stock
    /// .iso8601 strategy does not accept.
    private static func parse(_ raw: String) -> Date? {
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFraction.date(from: raw) { return date }

        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        return plain.date(from: raw)
    }
}

// MARK: - Request bodies

private struct StartBody: Encodable {
    let topic_id: String
}

private struct GradeBody: Encodable {
    let session_id: String
    let explanation_text: String
    /// "text" today. The endpoint already accepts "transcript", so speech input
    /// changes this string and nothing else.
    let source: String
}

// MARK: - Client

struct LearnClient {

    let baseURL: URL
    private let http: HTTPTransport
    private let grading: HTTPTransport

    /// Grading is an LLM call with reasoning effort, measured at 3.5-5 s and
    /// occasionally slower. The 8 s default that suits the digest endpoints —
    /// which only read a file — would time out a grade that was going to
    /// succeed, and throw away fifteen minutes of typing to do it.
    private static let gradingTimeout: TimeInterval = 90

    init(baseURL: URL) {
        self.baseURL = baseURL
        self.http    = HTTPTransport(baseURL: baseURL)
        self.grading = HTTPTransport(baseURL: baseURL, timeout: Self.gradingTimeout)
    }

    func drawTopic() async throws -> LearnTopic {
        let data = try await http.get(try http.url("learn/topic"))
        return try JSONDecoder().decode(LearnTopic.self, from: data)
    }

    func startSession(topicID: String) async throws -> LearnSession {
        try await http.postJSON("learn/session/start",
                                body: StartBody(topic_id: topicID),
                                as: LearnSession.self)
    }

    func grade(sessionID: String,
               explanation: String,
               source: String = "text") async throws -> Grade {
        try await grading.postJSON(
            "learn/grade",
            body: GradeBody(session_id: sessionID,
                            explanation_text: explanation,
                            source: source),
            as: Grade.self
        )
    }

    func fetchStats() async throws -> LearnStats {
        let data = try await http.get(try http.url("learn/stats"))
        return try JSONDecoder().decode(LearnStats.self, from: data)
    }
}
