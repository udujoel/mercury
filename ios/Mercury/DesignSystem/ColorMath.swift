import SwiftUI

/// sRGB and WCAG arithmetic, kept as plain values so a test can measure the
/// same numbers the renderer draws.
///
/// This exists because `Color` is opaque. A token declared as
/// `Color.white.opacity(0.45)` cannot be measured by a test, so
/// `MercuryColor.textTertiary` shipped at 4.41:1 — under the WCAG AA floor for
/// body text — with a comment claiming nothing either way. Everything here is
/// a `Double` until the last possible moment, and the `Color` is built from
/// explicit sRGB components rather than from `Color(hue:saturation:brightness:)`,
/// which resolves through UIKit's extended-sRGB space and would not be the
/// thing the maths below measured.
struct RGB: Equatable {
    let red: Double
    let green: Double
    let blue: Double

    init(red: Double, green: Double, blue: Double) {
        self.red = red
        self.green = green
        self.blue = blue
    }

    /// `"FBFAF8"` or `"#FBFAF8"`. Traps on malformed input: these are literals
    /// in this module, not user data, so a typo should fail the test run
    /// rather than silently render black.
    init(hex: String) {
        let s = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        precondition(s.count == 6, "expected RRGGBB, got \(hex)")
        let v = UInt32(s, radix: 16)!
        self.init(
            red: Double((v >> 16) & 0xFF) / 255,
            green: Double((v >> 8) & 0xFF) / 255,
            blue: Double(v & 0xFF) / 255
        )
    }

    var color: Color { Color(.sRGB, red: red, green: green, blue: blue, opacity: 1) }

    var hexString: String {
        String(format: "#%02X%02X%02X",
               Int((red * 255).rounded()), Int((green * 255).rounded()), Int((blue * 255).rounded()))
    }

    /// WCAG 2.1 relative luminance.
    var relativeLuminance: Double {
        0.2126 * ColorMath.linearise(red)
            + 0.7152 * ColorMath.linearise(green)
            + 0.0722 * ColorMath.linearise(blue)
    }

    /// This colour at `opacity` composited over `background`.
    ///
    /// Compositing happens in sRGB space, before the transfer function — which
    /// is what the render server does. Blending relative luminances instead is
    /// the standard way to get a plausible but wrong answer.
    func composited(opacity: Double, over background: RGB) -> RGB {
        RGB(red: red * opacity + background.red * (1 - opacity),
            green: green * opacity + background.green * (1 - opacity),
            blue: blue * opacity + background.blue * (1 - opacity))
    }
}

enum ColorMath {

    /// sRGB transfer function, WCAG 2.1 form.
    static func linearise(_ channel: Double) -> Double {
        channel <= 0.04045 ? channel / 12.92 : pow((channel + 0.055) / 1.055, 2.4)
    }

    /// WCAG 2.1 contrast ratio. Symmetric; ranges 1:1 to 21:1.
    static func contrast(_ a: RGB, _ b: RGB) -> Double {
        let la = a.relativeLuminance, lb = b.relativeLuminance
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }

    /// WCAG 2.1 minimum contrast for body text.
    static let aaBody = 4.5
    /// WCAG 2.1 minimum for text ≥18pt, or ≥14pt bold.
    static let aaLarge = 3.0
    /// WCAG 2.1 AAA for body text.
    static let aaaBody = 7.0

    // MARK: - HSB

    static func wrapHue(_ h: Double) -> Double {
        let x = h.truncatingRemainder(dividingBy: 1.0)
        return x < 0 ? x + 1 : x
    }

    static func rgb(hue: Double, saturation: Double, brightness: Double) -> RGB {
        let h = wrapHue(hue) * 6
        let sector = floor(h)
        let f = h - sector
        let p = brightness * (1 - saturation)
        let q = brightness * (1 - saturation * f)
        let t = brightness * (1 - saturation * (1 - f))
        switch Int(sector) % 6 {
        case 0:  return RGB(red: brightness, green: t, blue: p)
        case 1:  return RGB(red: q, green: brightness, blue: p)
        case 2:  return RGB(red: p, green: brightness, blue: t)
        case 3:  return RGB(red: p, green: q, blue: brightness)
        case 4:  return RGB(red: t, green: p, blue: brightness)
        default: return RGB(red: brightness, green: p, blue: q)
        }
    }

    /// Brightness at which `hue`/`saturation` lands on `luminance`.
    ///
    /// Bisection: relative luminance is monotonic in brightness at fixed hue
    /// and saturation, so twenty halvings land within ~1e-6 — far under one
    /// 8-bit code point, and cheap enough to run inside a view body.
    ///
    /// Solving for luminance rather than fixing brightness is the whole trick.
    /// Equal HSB brightness is not equal lightness: a blue swung toward cyan at
    /// constant brightness gains luminance fast, so a hue-jittered accent set
    /// at fixed brightness drifts in and out of AA. Pinning luminance makes
    /// contrast a property of the design instead of of the hue.
    static func brightness(hue: Double, saturation: Double, luminance target: Double) -> Double {
        var low = 0.0, high = 1.0
        for _ in 0..<20 {
            let mid = (low + high) / 2
            if rgb(hue: hue, saturation: saturation, brightness: mid).relativeLuminance < target {
                low = mid
            } else {
                high = mid
            }
        }
        return (low + high) / 2
    }

    /// Highest saturation at which `luminance` is still reachable, i.e. still
    /// reachable before brightness would have to exceed 1.0. Saturation pulls
    /// two channels down, so past this point the hue cannot be made bright
    /// enough to hit the target at all.
    static func maxSaturation(hue: Double, luminance target: Double) -> Double {
        var low = 0.0, high = 1.0
        for _ in 0..<20 {
            let mid = (low + high) / 2
            if rgb(hue: hue, saturation: mid, brightness: 1.0).relativeLuminance >= target {
                low = mid
            } else {
                high = mid
            }
        }
        return low
    }
}
