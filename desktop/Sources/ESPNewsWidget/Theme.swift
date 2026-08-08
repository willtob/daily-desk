//
//  Theme.swift — the palette, taken from the desktop it sits on.
//
//  Every colour in here is a literal sample from the wallpaper (the macOS
//  Ventura swirl), pulled by k-means over the image rather than eyeballed, so
//  the widget reads as part of the desk instead of as a window parked on it:
//
//      #FEE182  #FDD271  #F6B759  #F6AA4B      the amber ramp
//      #E48142  #D6683F  #CA5636               the coral end
//      #9AD7FC  #76ACDE  #4D7DB3               the blue edges
//      #E6EEF3                                 the near-white highlight
//
//  The shell is that coral end crushed nearly to black. A dark shell under
//  light, saturated cards is what both reference designs do, and it is what
//  lets a card read as a lit object rather than as a coloured rectangle.
//
//  These colours are now shared with firmware/src/news_ui.cpp. They used to
//  diverge on purpose — the device painted a small badge on a dark navy card
//  and needed eight hues that separate at badge size — but the firmware has
//  since taken this deck wholesale and paints whole cards in the tint too, so
//  it needs the same wallpaper palette to do it. Keys, labels and colours all
//  have to stay in sync now; see AreaStyle.
//
//  What still diverges is weight. LVGL ships Montserrat in a single weight, so
//  the device builds hierarchy from size and colour alone. Here the whole San
//  Francisco family is available, so the headline can carry the card by itself
//  the way it does in the reference layouts. The device also runs the ink
//  opacities a little higher than the values below, because 16-bit colour
//  quantises the blend and there is no bold to compensate with.
//

import SwiftUI

enum Theme {

    // MARK: - Shell
    //
    // #CA5636 (the wallpaper's deepest red) taken down to ~9% brightness. A
    // neutral charcoal would be the obvious choice and is subtly wrong: next
    // to a screen full of orange it reads blue.

    static let bg      = Color(hex: 0x241713)
    static let surface = Color(hex: 0x33201A)
    static let rule    = Color(hex: 0x4A2E24)
    static let dim     = Color(hex: 0xB89486)
    static let white   = Color(hex: 0xFFF3E6)
    static let accent  = Color(hex: 0xF6AA4B)

    // MARK: - Ink
    //
    // What goes *on* a card. Never pure black: on a saturated orange, #000
    // reads as a hole punched in the card rather than as text.

    static let ink     = Color(hex: 0x2E1608)

    /// Secondary text on a card. Opacity rather than a second colour, so it
    /// stays correct on every tint from #FEE182 to #D6683F.
    static func inkSoft(_ alpha: Double = 0.58) -> Color { ink.opacity(alpha) }

    // MARK: - Type
    //
    // Three roles. META is letter-spaced because that is what makes small
    // uppercase read as a tag rather than as shouted body text.

    static let metaSize:    CGFloat = 9.5
    static let bodySize:    CGFloat = 11.5
    static let titleSize:   CGFloat = 16
    static let displaySize: CGFloat = 19

    static let metaTracking: CGFloat = 0.9
    static let titleLeading: CGFloat = 1.5
    static let bodyLeading:  CGFloat = 3

    // MARK: - Layout

    static let pad:        CGFloat = 12
    static let headerH:    CGFloat = 32
    static let navH:       CGFloat = 34
    static let cardPad:    CGFloat = 14
    static let barH:       CGFloat = 4
    static let radius:     CGFloat = 10

    /// Corners are generous on purpose — they are the single strongest cue
    /// that separates "widget" from "window" on this desktop, where the stock
    /// Calendar and Weather widgets sit at roughly this radius.
    static let cardRadius:  CGFloat = 18
    static let shellRadius: CGFloat = 20

    // MARK: - Score band
    //
    // Cosine scores from the fitness function realistically land in this
    // band; the bar maps it across the full card width so differences are
    // visible. A pure 0...1 mapping renders every story as half a bar.

    static let scoreMin: Double = 0.25
    static let scoreMax: Double = 0.60

    /// Score to a 0...1 fraction of the available bar width, clamped.
    static func scoreFraction(_ score: Double) -> Double {
        let t = (score - scoreMin) / (scoreMax - scoreMin)
        return min(max(t, 0), 1)
    }
}

// MARK: - Interest areas

/// Badge text and card tint for an interest area.
///
/// The keys must match `matched_area` from the Python score node, and the
/// labels *and colours* are shared with `AREA_STYLES` in
/// firmware/src/news_ui.cpp — **adding an area to interests.yaml means adding
/// it in both places.** Both readers now paint the whole card in the tint, so
/// a colour that only works on one of them is a bug on the other.
///
/// Labels are abbreviated because they have to survive a narrow column. The
/// device has 140 px; this card has about 250.
struct AreaStyle {
    let label: String
    let tint: Color

    private static let table: [String: AreaStyle] = [
        "ai_open_source":     AreaStyle(label: "OPEN SRC", tint: Color(hex: 0xFEE182)),
        "ai_consciousness":   AreaStyle(label: "INTERP",   tint: Color(hex: 0xF6B759)),
        "classic_ml_applied": AreaStyle(label: "CLASSIC",  tint: Color(hex: 0x9AD7FC)),
        "big_tech_career":    AreaStyle(label: "BIG TECH", tint: Color(hex: 0x76ACDE)),
        "embedded_wearables": AreaStyle(label: "EMBEDDED", tint: Color(hex: 0xF6AA4B)),
        "startup_vc":         AreaStyle(label: "STARTUP",  tint: Color(hex: 0xFDD271)),
        "florida":            AreaStyle(label: "FLORIDA",  tint: Color(hex: 0xE48142)),
        "spain":              AreaStyle(label: "SPAIN",    tint: Color(hex: 0xD6683F)),
    ]

    /// The wallpaper's near-white. An unknown area gets a neutral card rather
    /// than vanishing, so a new area in interests.yaml shows up as un-styled
    /// instead of as a blank.
    private static let fallback = AreaStyle(label: "NEWS", tint: Color(hex: 0xE6EEF3))

    static func forArea(_ area: String?) -> AreaStyle {
        guard let area, let style = table[area] else { return fallback }
        return style
    }
}

// MARK: - Helpers

extension Color {
    /// 0xRRGGBB, so a sampled hex can be pasted in unchanged.
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
