//
//  Paragraphs.swift — turn a summary into the blocks a view can lay out.
//
//  Since Phase 9c the summarizer may emit a two-item Markdown subset —
//  `**bold**` and `- ` bullets — and separates paragraphs with a blank line.
//  See src/esp_news/markdown.py for why the subset is that small: this text is
//  also rendered by LVGL, which cannot do inline bold at all, and read aloud by
//  a speech model, which must never be given syntax.
//
//  ── Two sources of paragraphs, and the order matters ──────────────────────
//
//  Summaries cached under PROMPT_VERSION 1 are a single unbroken block, because
//  that prompt demanded plain prose with no breaks at all. Those still arrive,
//  and in a narrow column an 800-character block is a wall with nothing to find
//  your place by.
//
//  So: if the text brings its own breaks, they are used and nothing is
//  invented. Only when it has none does `insertBreaks` pace it by character
//  count. Doing this the other way round — always inserting — would cut the
//  model's own paragraphs in half.
//
//  ── Why inline-only parsing ───────────────────────────────────────────────
//
//  `.inlineOnlyPreservingWhitespace` is a safety decision, not a shortcut.
//  Full Markdown parsing would read a `#` at the start of a line as a heading
//  and `1.` as an ordered list, and this is a news digest: "C# developers" and
//  "1. Introduction" appear in real prose, as do the plain summaries already
//  cached, which were written with no thought for Markdown at all. Inline-only
//  interprets `**bold**` and leaves every block-level character alone. Bullets
//  are found here instead, where the rule can be exactly as narrow as needed.
//
//  CommonMark already declines to emphasise intra-word underscores, so
//  `snake_case_names` — a certainty in an AI and open-source digest — survives
//  without special handling.
//

import Foundation

/// One laid-out piece of a summary.
enum SummaryBlock {
    case paragraph(AttributedString)
    case bullet(AttributedString)
}

enum Paragraphs {

    /// Roughly how long a paragraph gets before the next sentence end is
    /// allowed to close it. Only used on text that arrived with no breaks.
    static let minLength = 170

    // MARK: - Blocks

    static func blocks(_ text: String) -> [SummaryBlock] {
        let source = text.contains("\n") ? text : insertBreaks(text)

        var out: [SummaryBlock] = []
        var pending: [String] = []

        func flush() {
            guard !pending.isEmpty else { return }
            let joined = pending.joined(separator: " ")
            if !joined.isEmpty { out.append(.paragraph(inline(joined))) }
            pending.removeAll()
        }

        for rawLine in source.components(separatedBy: "\n") {
            let line = rawLine.trimmingCharacters(in: .whitespaces)

            if line.isEmpty {
                flush()
            } else if let item = bulletBody(line) {
                flush()
                out.append(.bullet(inline(item)))
            } else {
                pending.append(line)
            }
        }
        flush()
        return out
    }

    /// Plain text with the subset removed — for the card excerpt, where two
    /// clipped lines of prose have no room to be anything but prose.
    static func plain(_ text: String) -> String {
        var out = ""
        for rawLine in text.components(separatedBy: "\n") {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty else { continue }
            out += (out.isEmpty ? "" : " ") + (bulletBody(line) ?? line)
        }
        return unbold(out)
    }

    // MARK: - Inline

    private static func inline(_ s: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: false,
            interpretedSyntax: .inlineOnlyPreservingWhitespace,
            failurePolicy: .returnPartiallyParsedIfPossible
        )
        return (try? AttributedString(markdown: s, options: options))
            ?? AttributedString(unbold(s))
    }

    /// The text of a bullet, or nil if this line is not one.
    ///
    /// Deliberately strict: a marker, then a space. "- " starts a bullet;
    /// "-5 degrees" and "well-known" do not.
    private static func bulletBody(_ line: String) -> String? {
        for marker in ["- ", "* ", "+ "] where line.hasPrefix(marker) {
            return String(line.dropFirst(marker.count))
        }
        return nil
    }

    /// Last-resort `**` removal, for the paths that show raw text.
    private static func unbold(_ s: String) -> String {
        s.replacingOccurrences(of: "**", with: "")
    }

    // MARK: - Fallback pacing for unbroken text

    /// Insert blank lines into a summary that arrived as one block.
    ///
    /// Ported from `paragraphize()` on the ESP32 panel (see the firmware-final
    /// tag), which is where the rule was worked out. This is the only copy now.
    ///
    /// The rule is conservative, because a missed break costs nothing and a
    /// break in the middle of a name is glaring: split only on a stop followed
    /// by a space and a capital, only after `minLength` characters, and never
    /// after a single capital letter — "U.S. Steel" and "J. Doe" are what that
    /// looks like to a sentence splitter. A decimal point never splits, because
    /// no space follows it.
    ///
    /// Characters rather than sentences are what pace it: the digest runs to
    /// 200-character sentences in Spanish and 60 in English, so a
    /// sentence-counting rule gives one slab in the first and confetti in the
    /// second.
    static func insertBreaks(_ text: String) -> String {
        let chars = Array(text)
        guard chars.count > minLength else { return text }

        var out = String()
        out.reserveCapacity(text.count + 32)

        var run = 0
        var i = 0

        while i < chars.count {
            out.append(chars[i])
            run += 1

            if run >= minLength, let gap = sentenceEnd(chars, at: i) {
                for k in 1..<gap { out.append(chars[i + k]) }
                i += gap + 1          // step over the space after the stop
                out.append("\n\n")
                run = 0
                continue
            }
            i += 1
        }
        return out
    }

    /// If `chars[i]` ends a sentence, the offset from it to the space that
    /// follows — 1 when the stop is bare, more when a closing quote sits
    /// between. Nil when this is not a sentence end.
    private static func sentenceEnd(_ chars: [Character], at i: Int) -> Int? {
        let c = chars[i]
        guard c == "." || c == "!" || c == "?" else { return nil }

        var j = i + 1
        guard j < chars.count else { return nil }

        if chars[j] == "\"" || chars[j] == "'" || chars[j] == "\u{201D}" || chars[j] == "\u{2019}" {
            j += 1
            guard j < chars.count else { return nil }
        }

        guard chars[j] == " ", j + 1 < chars.count else { return nil }

        // The next word has to look like the start of one. isUppercase covers
        // the accented capitals that open a Spanish sentence.
        let next = chars[j + 1]
        guard next.isUppercase || next.isNumber else { return nil }

        // An initial, not a full stop.
        if c == ".", i >= 1, chars[i - 1].isUppercase,
           i == 1 || chars[i - 2] == " " || chars[i - 2] == "." {
            return nil
        }

        return j - i
    }
}
