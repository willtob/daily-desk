//
//  LearnCheck.swift — drive a whole learning session against a live backend.
//
//  The snapshot harness renders the five screens and proves the layout. It
//  cannot prove that `missed_concepts` still decodes into `missedConcepts`,
//  because a field that fails to decode is not a crash — it is an empty list
//  on a screen that otherwise looks perfect. That failure would land at the
//  one moment in this app that costs fifteen minutes of typing to reach.
//
//  So this walks every endpoint in order against the real FastAPI app and
//  prints what came back, in the spirit of the firmware's ok/FAIL path suite
//  rather than a folder of images.
//
//      cd desktop && swift run ESPNewsWidget --learn-check http://127.0.0.1:8010
//
//  It spends one real grading call, which is the point — a mocked one would
//  only be re-testing this file's own assumptions.
//
//  **It writes a real graded session** into the backend's learn.db, so it
//  moves the streak and the rolling average. Fine on a scratch backend, worth
//  knowing before running it against the one keeping your streak.
//

import Foundation

@MainActor
enum LearnCheck {

    /// The session is opened on a *fixed* topic rather than on whatever
    /// /topic drew. The draw is random, so grading a canned explanation
    /// against it scores whatever the mismatch deserves — the first run of
    /// this file explained backpropagation to a logistic-regression prompt and
    /// scored 2/10, which is the rubric working correctly and tells you
    /// nothing about the client. Pinning the topic makes the score and the
    /// concept counts mean something.
    private static let gradedTopic = "backpropagation"

    /// Deliberately mediocre: complete enough to be gradeable, thin enough
    /// that a working rubric has to return a non-empty `missed_concepts`. An
    /// excellent answer would leave that list empty and the run could not tell
    /// "nothing missed" from "the field did not decode".
    private static let explanation = """
    Backpropagation is how the gradients get computed. You run a forward pass, \
    work out the loss, and then go backwards through the network applying the \
    chain rule layer by layer, so every weight ends up with a gradient saying \
    how much it contributed to the error. The optimiser then uses those \
    gradients to update the weights.
    """

    static func run(baseURL: URL) -> Never {
        let client = LearnClient(baseURL: baseURL)
        var failures = 0

        // Run loop rather than a semaphore. Blocking the main thread on a
        // semaphore and waiting for a @MainActor task to signal it is a
        // deadlock by construction: the task needs the very thread that is
        // parked. Running the loop lets the main queue keep servicing it, and
        // the task exits the process itself when it is finished.
        Task { @MainActor in
            print("learn-check → \(baseURL.absoluteString)\n")

            func step(_ name: String, _ body: () async throws -> String) async {
                do {
                    print("  ok    \(name.padded(20)) \(try await body())")
                } catch {
                    print("  FAIL  \(name.padded(20)) \(error.localizedDescription)")
                    failures += 1
                }
            }

            var session: LearnSession?

            await step("GET  /topic") {
                let t = try await client.drawTopic()
                return "\(t.topicID) — \(t.name) [\(t.difficulty)]"
            }

            await step("POST /session/start") {
                let s = try await client.startSession(topicID: gradedTopic)
                session = s
                return "session \(s.sessionID.prefix(8))… on \(s.topicID)"
            }

            await step("POST /grade") {
                guard let session else { throw CheckError.skipped("no session") }
                let started = Date()
                let g = try await client.grade(sessionID: session.sessionID,
                                               explanation: explanation)
                let secs = String(format: "%.1fs", Date().timeIntervalSince(started))
                // The three fields most likely to decode to nothing without
                // anyone noticing are counted here rather than dumped.
                return "\(g.score)/10 in \(secs) · counted=\(g.counted) "
                     + "· missed=\(g.missedConcepts.count) strengths=\(g.strengths.count)"
            }

            await step("GET  /stats") {
                let s = try await client.fetchStats()
                return "streak \(s.currentStreak) (best \(s.longestStreak)) · "
                     + "\(s.sessionsCompleted) sessions · avg \(s.rollingAverage.map { String(format: "%.1f", $0) } ?? "—")"
            }

            // Re-grading the same session must be refused, not silently
            // counted twice — that is the backend's guard and the widget
            // relies on it to keep a double-tapped Submit out of the streak.
            await step("POST /grade twice") {
                guard let session else { throw CheckError.skipped("no session") }
                do {
                    _ = try await client.grade(sessionID: session.sessionID,
                                               explanation: explanation)
                    throw CheckError.expected("a 409, got a second grade")
                } catch let NewsClientError.http(code, detail) where code == 409 {
                    return "refused: \(detail)"
                }
            }

            print(failures == 0 ? "\nall good" : "\n\(failures) failed")
            exit(failures == 0 ? 0 : 1)
        }
        RunLoop.main.run()
        // RunLoop.main.run() does not return; the Task above exits the process.
        fatalError("unreachable")
    }

    private enum CheckError: LocalizedError {
        case skipped(String)
        case expected(String)

        var errorDescription: String? {
            switch self {
            case let .skipped(why):  return "skipped — \(why)"
            case let .expected(what): return "expected \(what)"
            }
        }
    }
}

private extension String {
    func padded(_ n: Int) -> String {
        count >= n ? self : self + String(repeating: " ", count: n - count)
    }
}
