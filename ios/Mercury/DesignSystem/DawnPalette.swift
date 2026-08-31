import SwiftUI

/// The colour of the morning.
///
/// Mercury ships one edition a day, so the colour carries the date: seven
/// weekday anchors walking cold Monday round to warm Sunday. In the editorial
/// register (ADR-012) that colour is *ink*, not atmosphere — a rule, a section
/// label, an active tab — so every anchor has to hold up as a foreground on
/// paper, which is a far harder constraint than the ambient wash it replaced.
///
/// Two decisions here each replace something that was wrong.
///
/// **1. FNV-1a, not `Hasher`.** `AmbientBackground` derived its hue from
/// `Hasher().combine(seed)`. Swift seeds `Hasher` randomly per process, so its
/// docstring's promise — "same title always produces the same visual identity"
/// — held only until you killed the app. Measured, three launches of the
/// shipped code, seed `"greeting"`: hue 0.194, then 0.917, then 0.853. The
/// player's background was a different colour every launch, and `% 360`
/// collided often enough that `"greeting"` and `"calendar"` landed on the same
/// hue in the first process sampled. FNV-1a has no hidden state, so the value
/// in a test is the value on device, this launch and every launch after.
///
/// **2. Accents are pinned by luminance, not by brightness.** Equal HSB
/// brightness is not equal lightness. Holding brightness fixed while jittering
/// hue swings relative luminance enough to walk an accent in and out of AA.
/// Solving brightness for a target luminance instead makes contrast constant
/// across the whole band and across all seven days — every accent measures
/// 5.03:1 on paper, by construction rather than by luck.
enum DawnPalette {

    // MARK: - Luminance targets

    /// Accent luminance on light. The ceiling is 0.1833, where a foreground
    /// stops clearing 4.5:1 against `#FFFFFF` — the whiter of the two grounds
    /// an accent can land on. 0.150 leaves headroom and measures 5.03:1 on
    /// `surface`, 5.25:1 on `surfaceRaised`.
    static let lightAccentLuminance = 0.150

    /// Accent luminance on dark. The floor is 0.2007, where a foreground stops
    /// clearing 4.5:1 against `#1A1A1F`. 0.300 measures 6.28:1 and 5.78:1.
    static let darkAccentLuminance = 0.300

    /// Saturation ceiling. Above this the accents stop reading as editorial and
    /// start reading as a brand palette; the warm end also runs out of gamut.
    static let maxSaturation = 0.72

    /// Fraction of the reachable saturation to actually use. Sitting exactly at
    /// the gamut edge makes brightness pin to 1.0 and the hue go brittle, so
    /// back off slightly.
    static let saturationHeadroom = 0.94

    // MARK: - Weekday anchors

    struct Anchor: Equatable {
        /// 1 = Sunday … 7 = Saturday, matching `Calendar.Component.weekday`,
        /// which is locale-independent — it does not shift with `firstWeekday`.
        let weekday: Int
        let name: String
        let hue: Double
    }

    /// Cold Monday through warm Sunday. Only hue is stored: saturation and
    /// brightness are solved per appearance from the luminance targets above,
    /// because the value that keeps a colour legible differs between paper and
    /// night and cannot be one number.
    static let anchors: [Anchor] = [
        Anchor(weekday: 1, name: "Sunday",    hue: 0.1045),
        Anchor(weekday: 2, name: "Monday",    hue: 0.6232),
        Anchor(weekday: 3, name: "Tuesday",   hue: 0.6545),
        Anchor(weekday: 4, name: "Wednesday", hue: 0.7407),
        Anchor(weekday: 5, name: "Thursday",  hue: 0.8824),
        Anchor(weekday: 6, name: "Friday",    hue: 0.9848),
        Anchor(weekday: 7, name: "Saturday",  hue: 0.0657),
    ]

    static func anchor(weekday: Int) -> Anchor {
        anchors.first { $0.weekday == weekday } ?? anchors[1]
    }

    static func anchor(for date: Date, calendar: Calendar = .current) -> Anchor {
        anchor(weekday: calendar.component(.weekday, from: date))
    }

    // MARK: - The hash

    /// Widest departure from the day's anchor hue, either way. Wide enough that
    /// two surfaces in one edition are distinguishable, narrow enough that they
    /// are obviously the same morning.
    static let hueBand = 0.09

    /// FNV-1a, 64-bit, over UTF-8. A pure function of its input: no per-process
    /// seed, so a test can assert a literal and that literal stays true.
    static func fnv1a(_ seed: String) -> UInt64 {
        var hash: UInt64 = 14_695_981_039_346_656_037
        for byte in seed.utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 1_099_511_628_211
        }
        return hash
    }

    /// The hash as a fraction in `[0, 1)`.
    ///
    /// Takes the top 53 bits so the division is exact in `Double`. The low bits
    /// are dropped rather than folded: a modulo here would bias the result, and
    /// at the old code's `% 360` it collided often enough to be visible.
    static func unitFraction(_ seed: String) -> Double {
        Double(fnv1a(seed) >> 11) * (1.0 / 9_007_199_254_740_992.0)
    }

    /// Signed hue offset within `±hueBand`.
    static func hueOffset(seed: String) -> Double {
        (unitFraction(seed) * 2 - 1) * hueBand
    }

    // MARK: - Resolving an accent

    /// The accent at a given hue, solved for the appearance's luminance target.
    static func accentColor(hue: Double, dark isDark: Bool) -> RGB {
        let target = isDark ? darkAccentLuminance : lightAccentLuminance
        let h = ColorMath.wrapHue(hue)
        let saturation = min(maxSaturation,
                             ColorMath.maxSaturation(hue: h, luminance: target) * saturationHeadroom)
        let brightness = ColorMath.brightness(hue: h, saturation: saturation, luminance: target)
        return ColorMath.rgb(hue: h, saturation: saturation, brightness: brightness)
    }

    static func accent(hue: Double) -> AdaptiveColor {
        AdaptiveColor(light: accentColor(hue: hue, dark: false),
                      dark: accentColor(hue: hue, dark: true))
    }

    static func accent(weekday: Int, seed: String = "") -> AdaptiveColor {
        accent(hue: anchor(weekday: weekday).hue + (seed.isEmpty ? 0 : hueOffset(seed: seed)))
    }

    static func accent(for date: Date, seed: String = "", calendar: Calendar = .current) -> AdaptiveColor {
        accent(weekday: calendar.component(.weekday, from: date), seed: seed)
    }

    /// Every hue an accent can take on a given day, for tests that need to
    /// prove a floor holds across the whole band rather than at one sample.
    static func hueBandSamples(weekday: Int, steps: Int = 61) -> [Double] {
        let base = anchor(weekday: weekday).hue
        return (0..<steps).map { i in
            base - hueBand + 2 * hueBand * Double(i) / Double(steps - 1)
        }
    }
}
