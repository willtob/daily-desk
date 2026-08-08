//
//  Scrollable.swift — a ScrollView, or a plain clip when rendering offscreen.
//
//  ImageRenderer does not lay out scroll content: a snapshot of a view whose
//  body is a ScrollView comes back as an empty rectangle. Rather than keeping
//  a second copy of the layout for snapshots to render, the real views take a
//  `scrollable` flag and this swaps the container. What lands in the PNG is
//  then the actual view, which is the only way a snapshot is worth taking.
//

import SwiftUI

struct Scrollable<Content: View>: View {
    private let scrolls: Bool
    private let content: Content

    init(_ scrolls: Bool, @ViewBuilder content: () -> Content) {
        self.scrolls = scrolls
        self.content = content()
    }

    var body: some View {
        if scrolls {
            ScrollView(.vertical, showsIndicators: false) { content }
                .scrollBounceBehavior(.basedOnSize)
        } else {
            // The frame proposes the available height and anchors the content
            // to its top; anything longer spills past the bottom and is
            // clipped, which is what the panel itself shows before you
            // scroll. Without the frame the content is laid out at its
            // natural height and centred, and overruns both ends.
            VStack(spacing: 0) {
                content
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .clipped()
        }
    }
}
