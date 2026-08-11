//
//  FeedbackStore.swift — which stories have a verdict, and what happens when
//  the backend disagrees.
//
//  Kept apart from DigestStore because the two have opposite failure
//  behaviour. A failed digest poll is invisible and self-correcting: the panel
//  keeps the stories it already has and tries again in fifteen minutes. A
//  failed verdict is neither. The user has just done something deliberate and
//  is looking at the result, so the only honest thing is to put the UI back
//  where it was and make that visible.
//
//  ── Optimistic, and what that costs ───────────────────────────────────────
//
//  The heart appears on the frame the pull is released, before any request is
//  sent. On a LAN the round trip is a few milliseconds and nobody would ever
//  see a spinner, but "a few milliseconds" is not the case worth designing
//  for — the backend being down is, and that is exactly when a spinner would
//  sit there for the full eight-second timeout with the gesture apparently
//  unacknowledged.
//
//  So the state is written first and rolled back on failure. Rolling back is
//  only safe because the endpoints are idempotent and the whole state for an
//  article is one enum: there is no partial write to reconcile, and re-sending
//  the same verdict after a timeout cannot double-count. That is a property of
//  the API (docs/feedback-api.md), not an assumption made here.
//
//  ── Keys ──────────────────────────────────────────────────────────────────
//
//  Verdicts are keyed by the article URL exactly as digest.json spells it. The
//  backend canonicalizes URLs before storing, and hands back the same spelling
//  it was given, so the two agree. This does not try to reimplement that
//  normalization: a Swift copy of `_canonical_url` that drifted from the Python
//  one would show the wrong thumb on the right card, which is worse than not
//  matching at all.
//

import Foundation
import SwiftUI

@MainActor
final class FeedbackStore: ObservableObject {

    enum Verdict: String {
        case like
        case dislike
    }

    /// A verdict the backend refused, after the UI had already shown it.
    ///
    /// Carries an id so that the same article failing twice in a row is two
    /// events — the badge animates off this, and without the id the second
    /// rejection would be indistinguishable from the first and silently
    /// swallowed.
    struct Rejection: Equatable {
        let url: String
        let message: String
        let id = UUID()
    }

    @Published private(set) var verdicts: [String: Verdict] = [:]
    @Published private(set) var rejection: Rejection?

    func verdict(for url: String) -> Verdict? { verdicts[url] }

    // MARK: - Loading

    /// Read every recorded verdict. Called on launch and after each digest
    /// load, so a verdict recorded from another client shows up here too.
    ///
    /// A failure is swallowed on purpose: this is decoration on a panel whose
    /// main job is showing stories, and the empty-state — no thumbs — is
    /// exactly what a first run looks like anyway. The error path that matters
    /// is `apply`, where the user is watching.
    func load(using client: NewsClient) async {
        guard let loaded = try? await client.fetchVerdicts() else { return }
        verdicts = loaded.compactMapValues(Verdict.init(rawValue:))
    }

    /// Verdicts without a backend, for Snapshot. The rendered badge is the only
    /// way to check that a heart is legible at nine points, and a snapshot run
    /// has no server to have recorded one.
    func seed(_ seeded: [String: Verdict]) {
        verdicts = seeded
    }

    // MARK: - Recording

    /// Apply a pull's verdict to an article, optimistically.
    ///
    /// Pulling the same way twice clears the verdict rather than re-recording
    /// it, which is what makes a mis-pull recoverable with the same gesture
    /// that caused it — no menu, no second control to find.
    func apply(_ direction: Verdict, to url: String, using client: NewsClient) async {
        let previous = verdicts[url]
        let next: Verdict? = (previous == direction) ? nil : direction

        verdicts[url] = next          // shown now; the request has not left yet

        do {
            if let next {
                try await client.setVerdict(url: url, verdict: next.rawValue)
            } else {
                try await client.clearVerdict(url: url)
            }
            if rejection?.url == url { rejection = nil }
        } catch {
            // Put it back exactly as it was, including "no verdict" — assuming
            // the failure meant "stay liked" would leave the panel disagreeing
            // with the backend, which is the one outcome this must not produce.
            verdicts[url] = previous
            rejection = Rejection(url: url, message: error.localizedDescription)
        }
    }
}
