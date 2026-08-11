//
//  LearnView.swift — fifteen minutes, an explanation, and a grade.
//
//  Five screens over one LearnStore phase, laid out for the same 288 x 300
//  panel the deck lives in. Restraint is the brief: the shell is Theme.bg, the
//  accent is used for the timer trace, the score and one primary button, and
//  nothing else competes with it. Difficulty is a word rather than a colour —
//  the deck already spends the palette on interest-area tints, and a second
//  colour system on the same panel would read as noise.
//
//  **The timer is the widget's own border.** Not a ring in the middle of the
//  panel: an accent line traced around the shell's rounded rectangle that
//  empties as the fifteen minutes run down, which is what the iOS timer does on
//  the Dynamic Island. It lives in RootView's overlay, over the hairline stroke
//  that is already there, so it reads as the panel's edge lighting up rather
//  than as a control drawn inside it. That also buys back the vertical space a
//  ring would have cost, which on a 300 pt panel is the difference between the
//  countdown being the size of a headline and the size of a timer.
//

import SwiftUI

// MARK: - The border trace

/// The shell's edge, drawn in accent, emptying as `elapsed` goes 0...1.
///
/// Inset by half the line width so the stroke sits *inside* the panel — a
/// centred stroke is clipped in half by RootView's shellRadius clip, and comes
/// out looking like a 1.5 pt line that someone got wrong.
///
/// **Where the path starts was measured, not assumed.** SwiftUI does not
/// document it, and two frames of a naive `trim(from: 0, to: remaining)` looked
/// contradictory enough to be worth settling with pixels rather than with an
/// opinion. Sampling the four edges across a 90/60/30/5 % sweep says the path
/// begins at the *middle of the right edge* and runs clockwise — down the
/// right, along the bottom, up the left, across the top, back to where it
/// started. Trimming from zero therefore parks the gap on the right-hand edge,
/// which is not what the reference does.
///
/// Putting the gap at the top means starting the trim at the top's midpoint,
/// and that offset turns out to be a constant. Writing the straights as
/// `2(w-2r) + 2(h-2r)` and the four corners as `2πr`:
///
///     P      = 2[(w-2r) + (h-2r) + πr]
///     offset = 1.5[(w-2r) + (h-2r) + πr]      (mid-right → top-centre)
///     ratio  = 0.75, for every w, h and r
///
/// The measured value was 0.749. It holds for `.continuous` corners too — the
/// derivation only uses the four corners being congruent, not their shape — so
/// no GeometryReader is needed and resizing the panel cannot break it.
///
/// Because a trim cannot wrap past 1, the arc is drawn as one range or two.
///
/// (An earlier attempt rotated the whole shape by half a turn to steer this.
/// A rounded rectangle maps onto itself under that rotation, so it silently
/// moved the *start point* instead of the drawing, and stranded a stray
/// segment at the bottom-right corner.)
struct TimerBorder: View {

    let elapsed: Double
    var lineWidth: CGFloat = Theme.timerLine

    /// Top-centre, as a fraction of the path. See the derivation above.
    private static let topCentre = 0.75

    var body: some View {
        ZStack {
            ForEach(Self.arcs(remaining: max(1 - elapsed, 0)), id: \.lowerBound) { arc in
                RoundedRectangle(cornerRadius: Theme.shellRadius, style: .continuous)
                    .inset(by: lineWidth / 2)
                    .trim(from: arc.lowerBound, to: arc.upperBound)
                    .stroke(
                        Theme.accent,
                        style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
                    )
            }
        }
        .animation(.linear(duration: LearnStore.tickAnimation), value: elapsed)
        .allowsHitTesting(false)
    }

    /// The lit part of the border, starting at the top's midpoint and running
    /// clockwise for `remaining` of the way round. Split into two ranges when
    /// it wraps past the end of the path, because `trim` does not wrap.
    static func arcs(remaining: Double) -> [ClosedRange<Double>] {
        guard remaining > 0 else { return [] }
        let end = topCentre + min(remaining, 1)
        if end <= 1 { return [topCentre...end] }
        return [topCentre...1, 0...(end - 1)]
    }
}

// MARK: - Root

struct LearnView: View {

    @ObservedObject var store: LearnStore
    /// False when ImageRenderer is drawing this offscreen — see Scrollable.
    var scrolls = true

    @FocusState private var editorFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            switch store.phase {
            case .topic:      topicScreen
            case .timer:      timerScreen
            case .explaining: explainScreen
            case .grading:    gradingScreen
            case .result:     resultScreen
            case .stats:      statsScreen
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.horizontal, Theme.pad)
        .padding(.bottom, Theme.pad)
        .task { await store.drawTopicIfNeeded() }
    }

    // MARK: - 1. Topic

    private var topicScreen: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 0)

            if let topic = store.topic {
                capsule(store.difficultyText)
                    .padding(.bottom, 10)

                Text(topic.name)
                    .font(.system(size: Theme.titleSize, weight: .semibold))
                    .foregroundStyle(Theme.white)
                    .multilineTextAlignment(.center)
                    .lineSpacing(Theme.titleLeading)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(store.busy ? "Drawing a topic…" : "No topic")
                    .font(.system(size: Theme.bodySize))
                    .foregroundStyle(Theme.dim)
            }

            if let error = store.lastError {
                errorLine(error).padding(.top, 8)
            }

            Spacer(minLength: 0)

            primaryButton("Start 15 minutes") {
                Task { await store.start() }
            }
            .disabled(store.topic == nil || store.busy)
            .opacity(store.topic == nil || store.busy ? 0.4 : 1)

            HStack(spacing: 14) {
                textButton("Another topic") { Task { await store.drawTopic() } }
                    .disabled(store.busy)
                Text("·").foregroundStyle(Theme.dim.opacity(0.5))
                textButton("Stats") { Task { await store.showStats() } }
            }
            .padding(.top, 8)
        }
    }

    // MARK: - 2. Timer
    //
    // The reference's arrangement: controls flanking the countdown, nothing
    // else on screen. The topic stays visible in small type because forgetting
    // what you are about to explain, four minutes in, is a real thing.

    private var timerScreen: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 0)

            Text(store.topic?.name ?? "")
                .font(.system(size: Theme.metaSize + 1))
                .foregroundStyle(Theme.dim)
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .padding(.bottom, 14)

            HStack(spacing: 10) {
                circleButton(store.paused ? "play.fill" : "pause.fill") {
                    store.paused ? store.resume() : store.pause()
                }

                Text(store.clockText)
                    .font(.system(size: Theme.timerSize, weight: .light, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Theme.white)
                    .frame(maxWidth: .infinity)
                    // Paused is said by the numerals dimming rather than by a
                    // word appearing and shifting the layout.
                    .opacity(store.paused ? 0.45 : 1)
                    .animation(.easeOut(duration: 0.15), value: store.paused)

                circleButton("xmark") { store.cancel() }
            }

            Spacer(minLength: 0)

            primaryButton("Done — let me explain") { store.finishEarly() }
        }
    }

    // MARK: - 3. Explain

    private var explainScreen: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(store.topic?.name ?? "")
                .font(.system(size: Theme.metaSize + 1, weight: .semibold))
                .foregroundStyle(Theme.accent)
                .lineLimit(2)
                .padding(.top, 2)

            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .fill(Theme.surface)

                if scrolls {
                    // TextEditor draws its own opaque background, which on
                    // this shell is a white slab. Hiding it is the only way to
                    // put the editor on Theme.surface.
                    TextEditor(text: $store.explanation)
                        .font(.system(size: Theme.bodySize))
                        .lineSpacing(Theme.bodyLeading - 2)
                        .foregroundStyle(Theme.white)
                        .scrollContentBackground(.hidden)
                        .background(Color.clear)
                        .focused($editorFocused)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 6)
                } else {
                    // Offscreen. ImageRenderer cannot draw an NSTextView, and
                    // renders the editor as a "not allowed" placeholder that
                    // fills the panel — the same class of problem Scrollable
                    // exists for, and it hides the whole screen's layout. A
                    // Text at the editor's own metrics puts the real layout in
                    // the PNG; only the caret is missing.
                    Text(store.explanation)
                        .font(.system(size: Theme.bodySize))
                        .lineSpacing(Theme.bodyLeading - 2)
                        .foregroundStyle(Theme.white)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                        .padding(.horizontal, 13)
                        .padding(.vertical, 11)
                }

                if store.explanation.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Explain here.")
                            .foregroundStyle(Theme.dim.opacity(0.7))
                        Text("The mechanism is… It matters because… The catch is…")
                            .foregroundStyle(Theme.dim.opacity(0.45))
                    }
                    .font(.system(size: Theme.bodySize))
                    .padding(.horizontal, 13)
                    .padding(.vertical, 14)
                    .allowsHitTesting(false)
                }
            }
            .frame(maxHeight: .infinity)

            if let error = store.lastError {
                errorLine(error)
            }

            HStack(spacing: 8) {
                Text("\(store.explanation.count) characters")
                    .font(.system(size: Theme.metaSize))
                    .foregroundStyle(Theme.dim)

                Spacer(minLength: 4)

                textButton("Discard") { store.cancel() }
            }

            primaryButton("Submit for grading") {
                Task { await store.submit() }
            }
            .disabled(store.explanation.isEmpty || store.busy)
            .opacity(store.explanation.isEmpty || store.busy ? 0.4 : 1)
        }
        .onAppear { editorFocused = true }
    }

    // MARK: - 4. Grading

    private var gradingScreen: some View {
        VStack(spacing: 10) {
            Spacer(minLength: 0)

            // Same offscreen story as the editor: ImageRenderer draws an
            // AppKit spinner as a "not allowed" badge. A dot stands in.
            if scrolls {
                ProgressView()
                    .progressViewStyle(.circular)
                    .controlSize(.small)
                    .tint(Theme.accent)
            } else {
                Circle().fill(Theme.accent).frame(width: 8, height: 8)
            }

            Text("Grading your explanation")
                .font(.system(size: Theme.bodySize, weight: .semibold))
                .foregroundStyle(Theme.white)

            Text("A few seconds — the model reads it against a checklist.")
                .font(.system(size: Theme.metaSize))
                .foregroundStyle(Theme.dim)
                .multilineTextAlignment(.center)

            Spacer(minLength: 0)
        }
    }

    // MARK: - 5. Result
    //
    // Scannable, not a wall: the number first, then the one thing to fix, then
    // the lists. Missed concepts come before strengths on purpose — the point
    // of the exercise is the gap, and putting praise first buries it.

    private var resultScreen: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let grade = store.grade {
                scoreHeader(grade)
                    .padding(.bottom, 8)

                Scrollable(scrolls) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(grade.feedback)
                            .font(.system(size: Theme.bodySize))
                            .foregroundStyle(Theme.white)
                            .lineSpacing(Theme.bodyLeading - 2)
                            .fixedSize(horizontal: false, vertical: true)

                        if !grade.missedConcepts.isEmpty {
                            bulletSection("MISSED", grade.missedConcepts, tint: Theme.accent)
                        }
                        if !grade.strengths.isEmpty {
                            bulletSection("STRENGTHS", grade.strengths, tint: Theme.dim)
                        }
                    }
                    .padding(.bottom, 8)
                }
            }

            HStack(spacing: 14) {
                textButton("Stats") { Task { await store.showStats() } }
                Spacer(minLength: 4)
            }
            .padding(.bottom, 6)

            primaryButton("Next topic") { Task { await store.newSession() } }
        }
    }

    private func scoreHeader(_ grade: Grade) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("\(grade.score)")
                    .font(.system(size: Theme.scoreSize, weight: .semibold, design: .rounded))
                    .foregroundStyle(Theme.accent)
                Text("/ 10")
                    .font(.system(size: Theme.bodySize))
                    .foregroundStyle(Theme.dim)

                Spacer(minLength: 6)

                // Whether it held the streak up is the second thing worth
                // knowing after the number, and it is not derivable from the
                // score without knowing the threshold.
                capsule(grade.counted ? "COUNTED" : "BELOW \(grade.passScore)")
            }

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.rule)
                    Capsule()
                        .fill(Theme.accent)
                        .frame(width: geo.size.width * Double(grade.score) / 10)
                }
            }
            .frame(height: Theme.barH)
        }
    }

    // MARK: - 6. Stats

    private var statsScreen: some View {
        VStack(alignment: .leading, spacing: 0) {
            Spacer(minLength: 0)

            if let stats = store.stats {
                HStack(spacing: 0) {
                    statBlock("\(stats.currentStreak)", "CURRENT STREAK", accent: true)
                    statBlock("\(stats.longestStreak)", "LONGEST")
                }
                .padding(.bottom, 14)

                statRow("Sessions", "\(stats.sessionsCompleted)")
                statRow("Last \(stats.rollingWindow)", format(stats.rollingAverage))
                statRow("All time", format(stats.averageAllTime))
                if let last = stats.lastSessionText {
                    statRow("Last session", last)
                }
            } else if let error = store.lastError {
                errorLine(error)
            } else {
                Text("Loading…")
                    .font(.system(size: Theme.bodySize))
                    .foregroundStyle(Theme.dim)
            }

            Spacer(minLength: 0)

            primaryButton("Back") { store.leaveStats() }
        }
    }

    private func statBlock(_ value: String, _ label: String, accent: Bool = false) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.system(size: Theme.scoreSize, weight: .semibold, design: .rounded))
                .foregroundStyle(accent ? Theme.accent : Theme.white)
            Text(label)
                .font(.system(size: Theme.metaSize))
                .tracking(Theme.metaTracking)
                .foregroundStyle(Theme.dim)
        }
        .frame(maxWidth: .infinity)
    }

    private func statRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
                .font(.system(size: Theme.bodySize))
                .foregroundStyle(Theme.dim)
            Spacer(minLength: 8)
            Text(value)
                .font(.system(size: Theme.bodySize, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(Theme.white)
        }
        .padding(.vertical, 3)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.rule).frame(height: 1)
        }
    }

    private func format(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%.1f", value)
    }

    // MARK: - Shared pieces

    private func bulletSection(_ title: String, _ items: [String], tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: Theme.metaSize, weight: .bold))
                .tracking(Theme.metaTracking)
                .foregroundStyle(tint)

            ForEach(items, id: \.self) { item in
                HStack(alignment: .top, spacing: 5) {
                    Text("•").foregroundStyle(tint.opacity(0.7))
                    Text(item)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.system(size: Theme.bodySize))
                .foregroundStyle(Theme.white.opacity(0.9))
            }
        }
    }

    private func capsule(_ text: String) -> some View {
        Text(text)
            .font(.system(size: Theme.metaSize, weight: .bold))
            .tracking(Theme.metaTracking)
            .foregroundStyle(Theme.dim)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(Theme.surface))
    }

    private func errorLine(_ text: String) -> some View {
        Text(text)
            .font(.system(size: Theme.metaSize))
            .foregroundStyle(Theme.accent)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func primaryButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: Theme.bodySize, weight: .semibold))
                .foregroundStyle(Theme.bg)
                .frame(maxWidth: .infinity)
                .frame(height: Theme.navH)
                .background(
                    RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                        .fill(Theme.accent)
                )
        }
        .buttonStyle(.plain)
    }

    private func textButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: Theme.metaSize, weight: .semibold))
                .foregroundStyle(Theme.dim)
        }
        .buttonStyle(.plain)
    }

    /// The pause and cancel controls, as in the reference. Cancel is
    /// deliberately not red: one accent, and a destructive colour here would be
    /// the loudest thing on a panel whose point is a calm countdown.
    private func circleButton(_ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(Theme.dim)
                .frame(width: 34, height: 34)
                .background(Circle().fill(Theme.surface))
        }
        .buttonStyle(.plain)
    }
}
