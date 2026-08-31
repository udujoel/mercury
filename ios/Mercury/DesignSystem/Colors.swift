import SwiftUI
import UIKit

/// A token that resolves to a different colour per appearance.
///
/// The structural problem this fixes: every colour in the app was a hardcoded
/// literal — `Color.black` for the canvas, `Color.white.opacity(0.72)` for
/// secondary text — so the system was not merely dark-styled, it was
/// *incapable* of light mode. There was no seam to put a second value behind.
/// Holding both values in one token, as data, is what makes ADR-012's light
/// register possible at all, and it is why tests can measure both appearances
/// without instantiating a view.
struct AdaptiveColor {
    let light: RGB
    let dark: RGB

    init(light: RGB, dark: RGB) {
        self.light = light
        self.dark = dark
    }

    init(light: String, dark: String) {
        self.init(light: RGB(hex: light), dark: RGB(hex: dark))
    }

    /// Same colour in both appearances — for the handful of things that are
    /// genuinely appearance-independent, like text on a saturated accent fill.
    init(fixed: String) {
        let c = RGB(hex: fixed)
        self.init(light: c, dark: c)
    }

    var color: Color {
        Color(UIColor { traits in
            UIColor(traits.userInterfaceStyle == .dark ? dark.color : light.color)
        })
    }

    func resolved(dark isDark: Bool) -> RGB { isDark ? dark : light }
}

/// Mercury's semantic colour tokens.
///
/// Named by role, not by value: `surface`, `ink`, `hairline`. A view asking for
/// `MercuryColor.ink` gets legible text in either appearance without knowing
/// which one it is in, which is the point — the old names (`textPrimary` =
/// `.white`) encoded the answer for exactly one appearance.
///
/// Every ratio quoted below is measured by `ContrastTests`, not estimated.
/// If you change a value, that file tells you what it cost.
enum MercuryColor {

    // MARK: - Grounds

    /// The page. Warm off-white on light — paper, not screen — and a near-black
    /// that is not pure black on dark, so raised surfaces have somewhere to go.
    static let surface = AdaptiveColor(light: "FBFAF8", dark: "111114")

    /// Cards and sheets sitting on `surface`.
    static let surfaceRaised = AdaptiveColor(light: "FFFFFF", dark: "1A1A1F")

    /// Rules, dividers, card edges. Structure rather than text, so WCAG's 3:1
    /// for UI components does not apply — it identifies no control. Measured
    /// 1.23:1 (light) and 1.36:1 (dark) against `surface`, which is the range a
    /// 1px rule reads at without becoming a stripe.
    static let hairline = AdaptiveColor(light: "E6E3DE", dark: "2C2C33")

    // MARK: - Ink

    /// Body and headline text. 16.65:1 on light surface, 16.69:1 on dark.
    static let ink = AdaptiveColor(light: "191A1F", dark: "F2F1EE")

    /// Supporting text — bylines, timecodes, counts. 6.45:1 light, 8.00:1 dark;
    /// clears AA body on both grounds.
    static let inkMuted = AdaptiveColor(light: "5A5B66", dark: "A8A8B2")

    /// The quietest legible ink. 4.91:1 light, 5.21:1 dark.
    ///
    /// Retuned from the values this layer was first drafted with (`#8A8B96` /
    /// `#77777F`), which measured 3.24:1 and 3.90:1 — under AA body, the same
    /// defect as the `white.opacity(0.45)` tertiary they replaced. Darkening
    /// the light value and lightening the dark one costs some delicacy and buys
    /// a token that can hold small type anywhere without a case-by-case check.
    static let inkFaint = AdaptiveColor(light: "6C6D76", dark: "86868F")

    /// Text and glyphs drawn *on* an accent fill. Accents are pinned to a fixed
    /// luminance in each appearance, so which of these reads is decided once
    /// here rather than per call site.
    static let inkOnAccent = AdaptiveColor(light: "FFFFFF", dark: "111114")

    // MARK: - Accent

    /// Today's accent — the weekday colour. See `DawnPalette`.
    static var accent: AdaptiveColor { DawnPalette.accent(for: Date()) }

    static func accent(for date: Date, calendar: Calendar = .current) -> AdaptiveColor {
        DawnPalette.accent(for: date, calendar: calendar)
    }

    // MARK: - Geometry

    /// Card corner radius. 28 came from PRD 3.3's Huxe-derived deck; the
    /// editorial register wants a squarer card, and 20 reads as paper rather
    /// than as a widget at the sizes task 2 uses.
    static let cardCornerRadius: CGFloat = 20

    // MARK: - Testing seam

    /// Every semantic ink token, so `ContrastTests` measures the set rather
    /// than whichever ones someone remembered to add.
    static let inkTokensForTesting: [(String, AdaptiveColor)] = [
        ("ink", ink), ("inkMuted", inkMuted), ("inkFaint", inkFaint),
    ]

    static let groundTokensForTesting: [(String, AdaptiveColor)] = [
        ("surface", surface), ("surfaceRaised", surfaceRaised),
    ]

    // MARK: - Superseded names

    // ADR-012 replaced the dark-only palette. These forward to the semantic
    // tokens so `Features/` keeps building while tasks 2, 3 and 7 migrate.
    // Migration is not mechanical for the two marked below — read the note.

    @available(*, deprecated, renamed: "surface",
               message: "ADR-012: the canvas is no longer true black. Use MercuryColor.surface.")
    static var canvas: Color { surface.color }

    @available(*, deprecated, renamed: "ink",
               message: "ADR-012: use MercuryColor.ink.")
    static var textPrimary: Color { ink.color }

    @available(*, deprecated, renamed: "inkMuted",
               message: "ADR-012: use MercuryColor.inkMuted.")
    static var textSecondary: Color { inkMuted.color }

    @available(*, deprecated, renamed: "inkFaint",
               message: "ADR-012: use MercuryColor.inkFaint.")
    static var textTertiary: Color { inkFaint.color }

    // The four per-family accents are gone: one edition has one colour, and a
    // card's identity now comes from its position and type rather than from a
    // hue of its own. All four resolve to today's accent, which is correct but
    // makes the four cards identical — task 2 should drop the distinction
    // rather than keep four names for one value.
    @available(*, deprecated, renamed: "accent",
               message: "ADR-012: one edition, one colour. Use MercuryColor.accent.")
    static var calendarAccent: Color { accent.color }

    @available(*, deprecated, renamed: "accent",
               message: "ADR-012: one edition, one colour. Use MercuryColor.accent.")
    static var inboxAccent: Color { accent.color }

    @available(*, deprecated, renamed: "accent",
               message: "ADR-012: one edition, one colour. Use MercuryColor.accent.")
    static var weatherAccent: Color { accent.color }

    @available(*, deprecated, renamed: "accent",
               message: "ADR-012: one edition, one colour. Use MercuryColor.accent.")
    static var greetingAccent: Color { accent.color }
}
