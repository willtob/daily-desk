//
//  SummaryBody.swift — the story's prose, as blocks rather than one Text.
//
//  A single `Text` cannot lay out a bullet: the marker has to sit outside the
//  text column so that a wrapped second line indents under the first rather
//  than under the dot. So the summary is split into blocks (see Paragraphs)
//  and each is laid out on its own, with bullets as a two-column row aligned
//  on the first baseline.
//
//  Bold arrives already resolved. `AttributedString` carries the emphasis as an
//  inline presentation intent and SwiftUI's `Text` renders it against whatever
//  font is in scope, so the weight follows the size set here rather than being
//  hard-coded — which is what keeps it right if the type scale moves.
//

import SwiftUI

struct SummaryBody: View {

    let text: String
    var size: CGFloat = Theme.bodySize
    var leading: CGFloat = Theme.bodyLeading
    var color: Color = Theme.ink

    private var blocks: [SummaryBlock] { Paragraphs.blocks(text) }

    var body: some View {
        VStack(alignment: .leading, spacing: paragraphGap) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case .paragraph(let content):
                    Text(content)
                        .lineSpacing(leading)
                        .fixedSize(horizontal: false, vertical: true)

                case .bullet(let content):
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("•")
                            .foregroundStyle(color.opacity(0.55))
                        Text(content)
                            .lineSpacing(leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .font(.system(size: size))
        .foregroundStyle(color)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Proportional to the type size rather than a constant, so the gap still
    /// reads as a paragraph break and not as a dropped line if the scale moves.
    ///
    /// It has to clear `leading` by a visible margin, not merely exceed it —
    /// at 0.62 the gap came out at 7 pt against 5 pt of line spacing, which
    /// reads as one slightly loose line rather than as a new paragraph.
    private var paragraphGap: CGFloat { max(size * 1.0, leading * 2) }
}
