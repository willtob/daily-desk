//
//  Theme.swift — the firmware's look, ported.
//
//  The palette, the badge table and the score band are copied from
//  firmware/src/news_ui.cpp rather than reinvented, so the panel and the
//  device read as the same reader. If an interest area is added to
//  interests.yaml it has to be added in both places — see AREA_STYLES there.
//
//  The one deliberate divergence is weight. LVGL ships Montserrat in a single
//  weight, so the device builds hierarchy from size, colour and spacing alone.
//  Here we have the whole San Francisco family, so headlines get real weight
//  and the sizes can stay closer together.
//

import SwiftUI

enum Theme {

    // MARK: - Palette

    static let bg          = Color(hex: 0x1A1A2E)
    static let card        = Color(hex: 0x232342)
    static let cardHover   = Color(hex: 0x32325C)
    static let white       = Color(hex: 0xFFFFFF)
    static let dim         = Color(hex: 0x8A8AA3)
    static let rule        = Color(hex: 0x32325C)
    static let accent      = Color(hex: 0xE94560)

    // MARK: - Type scale
    //
    // Three roles, not five sizes used at random — same discipline as the
    // device. META is letter-spaced because that is what makes small
    // uppercase text read as a tag instead of as shouted body text.

    static let metaSize:    CGFloat = 10
    static let bodySize:    CGFloat = 13
    static let titleSize:   CGFloat = 15
    static let displaySize: CGFloat = 19

    static let metaTracking:  CGFloat = 0.8
    static let titleLeading:  CGFloat = 1
    static let bodyLeading:   CGFloat = 4

    // MARK: - Layout

    static let pad:      CGFloat = 10
    static let headerH:  CGFloat = 44
    static let cardPad:  CGFloat = 10
    static let barH:     CGFloat = 3
    static let radius:   CGFloat = 8

    // MARK: - Score band
    //
    // Cosine scores from the fitness function realistically land in this
    // band; the bar maps it to the full card width so differences are
    // actually visible. A pure 0..1 mapping would render every story as
    // roughly half a bar.

    static let scoreMin: Double = 0.25
    static let scoreMax: Double = 0.60

    /// Score to a 0...1 fraction of the available bar width, clamped.
    static func scoreFraction(_ score: Double) -> Double {
        let t = (score - scoreMin) / (scoreMax - scoreMin)
        return min(max(t, 0), 1)
    }
}

// MARK: - Interest areas

/// Badge text and colour for an interest area.
///
/// The keys must match `matched_area` from the Python score node. The labels
/// are abbreviated because they have to survive a narrow column — the device
/// has 144 px, and this panel is not much more generous.
struct AreaStyle {
    let label: String
    let color: Color

    private static let table: [String: AreaStyle] = [
        "ai_open_source":     AreaStyle(label: "OPEN SRC", color: Color(hex: 0x4CAF50)),
        "ai_consciousness":   AreaStyle(label: "INTERP",   color: Color(hex: 0xA97BF7)),
        "classic_ml_applied": AreaStyle(label: "CLASSIC",  color: Color(hex: 0x26C6DA)),
        "big_tech_career":    AreaStyle(label: "BIG TECH", color: Color(hex: 0x42A5F5)),
        "embedded_wearables": AreaStyle(label: "EMBEDDED", color: Color(hex: 0xFF9800)),
        "startup_vc":         AreaStyle(label: "STARTUP",  color: Color(hex: 0xFFD54F)),
        "florida":            AreaStyle(label: "FLORIDA",  color: Color(hex: 0xE94560)),
        "spain":              AreaStyle(label: "SPAIN",    color: Color(hex: 0xF06292)),
    ]

    private static let fallback = AreaStyle(label: "NEWS", color: Theme.dim)

    /// Never fails: an unknown area gets the neutral NEWS badge rather than
    /// vanishing, so a new area in interests.yaml shows up as un-styled
    /// instead of as a blank card.
    static func forArea(_ area: String?) -> AreaStyle {
        guard let area, let style = table[area] else { return fallback }
        return style
    }
}

// MARK: - Helpers

extension Color {
    /// 0xRRGGBB, to keep the ported constants byte-identical to the C.
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red:   Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >>  8) & 0xFF) / 255,
            blue:  Double( hex        & 0xFF) / 255,
            opacity: 1
        )
    }
}
