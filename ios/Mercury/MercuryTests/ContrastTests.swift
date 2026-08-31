import XCTest
@testable import Mercury

/// Contrast is measured here, not claimed in a comment.
///
/// `MercuryColor.textTertiary` shipped at `white.opacity(0.45)` — 4.41:1 on the
/// canvas, under the 4.5:1 WCAG AA floor for body text — and nothing in the
/// codebase said so either way. The same defect then reappeared in the first
/// draft of the light layer: `inkFaint` at `#8A8B96` measured 3.24:1 on paper.
/// Both were caught by running these numbers rather than by looking. The point
/// of this file is that the next person to nudge a token is told by a failing
/// test, not by a user.
final class ContrastTests: XCTestCase {

    // MARK: - The maths itself

    /// If the ratio function is wrong, every other assertion in this file is
    /// wrong in the same direction and they all still agree with each other.
    /// These are fixed points of WCAG 2.1 that can be checked by hand.
    func testContrastFormulaAgainstKnownFixedPoints() {
        let white = RGB(hex: "FFFFFF"), black = RGB(hex: "000000")
        XCTAssertEqual(white.relativeLuminance, 1.0, accuracy: 1e-9)
        XCTAssertEqual(black.relativeLuminance, 0.0, accuracy: 1e-9)

        // White on black is the maximum possible ratio, exactly 21:1.
        XCTAssertEqual(ColorMath.contrast(white, black), 21.0, accuracy: 1e-9)
        // A colour against itself is 1:1, and the ratio is symmetric.
        XCTAssertEqual(ColorMath.contrast(white, white), 1.0, accuracy: 1e-12)
        XCTAssertEqual(ColorMath.contrast(white, black), ColorMath.contrast(black, white), accuracy: 1e-12)

        // #767676 on white is the canonical 4.54:1 worked example from the
        // WCAG quick reference — the standard smoke test for this formula.
        XCTAssertEqual(ColorMath.contrast(RGB(hex: "767676"), white), 4.54, accuracy: 0.01)
        // …and #595959 on white, the other published one.
        XCTAssertEqual(ColorMath.contrast(RGB(hex: "595959"), white), 7.00, accuracy: 0.01)

        // The transfer function's knee, both sides.
        XCTAssertEqual(ColorMath.linearise(0.0), 0.0, accuracy: 1e-12)
        XCTAssertEqual(ColorMath.linearise(1.0), 1.0, accuracy: 1e-12)
        XCTAssertEqual(ColorMath.linearise(0.04), 0.04 / 12.92, accuracy: 1e-12)
    }

    func testCompositingHappensBeforeTheTransferFunction() {
        // Blending relative luminances instead of sRGB channels is the classic
        // way to get a plausible wrong answer. White at 50% over black
        // composites to sRGB 0.5, whose luminance is ~0.2140, not 0.5.
        let half = RGB(hex: "FFFFFF").composited(opacity: 0.5, over: RGB(hex: "000000"))
        XCTAssertEqual(half.relativeLuminance, ColorMath.linearise(0.5), accuracy: 1e-12)
        XCTAssertEqual(half.relativeLuminance, 0.2140, accuracy: 0.0005)
    }

    func testHexParsingRoundTrips() {
        for hex in ["#FBFAF8", "#111114", "#000000", "#FFFFFF", "#3C64D6"] {
            XCTAssertEqual(RGB(hex: hex).hexString, hex)
        }
        XCTAssertEqual(RGB(hex: "FBFAF8"), RGB(hex: "#FBFAF8"))
    }

    // MARK: - Ink on its grounds, both appearances

    func testEveryInkTokenClearsAABodyOnEveryGroundInBothAppearances() {
        for (inkName, ink) in MercuryColor.inkTokensForTesting {
            for (groundName, ground) in MercuryColor.groundTokensForTesting {
                for isDark in [false, true] {
                    let ratio = ColorMath.contrast(ink.resolved(dark: isDark), ground.resolved(dark: isDark))
                    XCTAssertGreaterThanOrEqual(
                        ratio, ColorMath.aaBody,
                        "\(inkName) on \(groundName) [\(isDark ? "dark" : "light")] measured \(ratio):1"
                    )
                }
            }
        }
    }

    /// The measured numbers, pinned. Any retune shows up here as a diff with a
    /// number attached rather than as a silent shift.
    func testInkContrastRatiosAreWhatTheTokensClaim() {
        func ratio(_ ink: AdaptiveColor, _ ground: AdaptiveColor, dark: Bool) -> Double {
            ColorMath.contrast(ink.resolved(dark: dark), ground.resolved(dark: dark))
        }
        XCTAssertEqual(ratio(MercuryColor.ink, MercuryColor.surface, dark: false), 16.65, accuracy: 0.02)
        XCTAssertEqual(ratio(MercuryColor.ink, MercuryColor.surface, dark: true), 16.69, accuracy: 0.02)
        XCTAssertEqual(ratio(MercuryColor.inkMuted, MercuryColor.surface, dark: false), 6.45, accuracy: 0.02)
        XCTAssertEqual(ratio(MercuryColor.inkMuted, MercuryColor.surface, dark: true), 8.00, accuracy: 0.02)
        XCTAssertEqual(ratio(MercuryColor.inkFaint, MercuryColor.surface, dark: false), 4.91, accuracy: 0.02)
        XCTAssertEqual(ratio(MercuryColor.inkFaint, MercuryColor.surface, dark: true), 5.21, accuracy: 0.02)
    }

    func testTheDraftInkFaintValuesWouldHaveFailed() {
        // Explicit regression on the values this layer was first drafted with.
        // If someone lightens inkFaint back toward them for delicacy, this
        // states the cost in the units that matter.
        XCTAssertLessThan(ColorMath.contrast(RGB(hex: "8A8B96"), MercuryColor.surfaceRaised.light),
                          ColorMath.aaBody)
        XCTAssertLessThan(ColorMath.contrast(RGB(hex: "77777F"), MercuryColor.surfaceRaised.dark),
                          ColorMath.aaBody)
        XCTAssertGreaterThanOrEqual(
            ColorMath.contrast(MercuryColor.inkFaint.light, MercuryColor.surfaceRaised.light),
            ColorMath.aaBody)
    }

    func testTheSupersededDarkOnlyTertiaryWouldHaveFailed() {
        // white.opacity(0.45) on true black — the value ADR-012 replaced.
        let old = RGB(hex: "FFFFFF").composited(opacity: 0.45, over: RGB(hex: "000000"))
        XCTAssertEqual(ColorMath.contrast(old, RGB(hex: "000000")), 4.41, accuracy: 0.01)
        XCTAssertLessThan(ColorMath.contrast(old, RGB(hex: "000000")), ColorMath.aaBody)
    }

    // MARK: - Accents as foreground

    func testEveryAccentClearsAABodyOnPaperAndOnNightAcrossTheWholeHueBand() {
        // The constraint that sets both luminance targets. An accent sets
        // section labels and active tabs — foreground text on a ground — so
        // 4.5:1 is the bar, and it has to hold at every hue the band can
        // produce, not just at the seven anchors.
        var worstLight = Double.infinity, worstDark = Double.infinity
        for weekday in 1...7 {
            for hue in DawnPalette.hueBandSamples(weekday: weekday) {
                for (name, ground) in MercuryColor.groundTokensForTesting {
                    let light = ColorMath.contrast(DawnPalette.accentColor(hue: hue, dark: false),
                                                   ground.light)
                    let dark = ColorMath.contrast(DawnPalette.accentColor(hue: hue, dark: true),
                                                  ground.dark)
                    XCTAssertGreaterThanOrEqual(light, ColorMath.aaBody,
                                                "light accent hue \(hue) on \(name): \(light):1")
                    XCTAssertGreaterThanOrEqual(dark, ColorMath.aaBody,
                                                "dark accent hue \(hue) on \(name): \(dark):1")
                    worstLight = min(worstLight, light)
                    worstDark = min(worstDark, dark)
                }
            }
        }
        // Constant by construction: luminance is pinned, so the worst case is
        // the same everywhere rather than dipping at the awkward hues.
        XCTAssertEqual(worstLight, 5.03, accuracy: 0.02)
        XCTAssertEqual(worstDark, 5.78, accuracy: 0.02)
    }

    func testHoldingBrightnessInsteadOfLuminanceWouldHaveFailed() {
        // The rejected design: fix HSB brightness per anchor and let the hue
        // jitter ride on top. Equal brightness is not equal lightness — a blue
        // swung toward cyan gains luminance fast — so the accent walks out of
        // AA at some hues and not others. Reproduced here so the reason for
        // the luminance solve survives as something executable.
        var worst = Double.infinity
        for anchor in DawnPalette.anchors {
            for hue in DawnPalette.hueBandSamples(weekday: anchor.weekday, steps: 37) {
                let naive = ColorMath.rgb(hue: hue, saturation: 0.72, brightness: 0.80)
                worst = min(worst, ColorMath.contrast(naive, MercuryColor.surfaceRaised.light))
            }
        }
        XCTAssertLessThan(worst, ColorMath.aaBody,
                          "the naive fixed-brightness design should fail AA somewhere in the band")
    }

    func testInkOnAccentIsLegibleInBothAppearances() {
        // Text drawn on an accent fill — a pill, a filled tab.
        for weekday in 1...7 {
            for isDark in [false, true] {
                let accent = DawnPalette.accent(weekday: weekday).resolved(dark: isDark)
                let ratio = ColorMath.contrast(MercuryColor.inkOnAccent.resolved(dark: isDark), accent)
                XCTAssertGreaterThanOrEqual(ratio, ColorMath.aaBody,
                                            "inkOnAccent over weekday \(weekday) [\(isDark)]: \(ratio):1")
            }
        }
    }

    // MARK: - Hairline

    func testHairlineIsVisibleWithoutPretendingToBeAControlBoundary() {
        // WCAG 1.4.11's 3:1 covers components needed to identify a control; a
        // decorative rule is not one, so this asserts visibility, and asserts
        // the measured value so a change is deliberate.
        for (isDark, expectedSurface) in [(false, 1.23), (true, 1.36)] {
            let ratio = ColorMath.contrast(MercuryColor.hairline.resolved(dark: isDark),
                                           MercuryColor.surface.resolved(dark: isDark))
            XCTAssertEqual(ratio, expectedSurface, accuracy: 0.02)
            XCTAssertGreaterThan(ratio, 1.15, "hairline would be invisible")
        }
    }

    // MARK: - The ambient wash

    func testTheAmbientWashCannotPushInkOutOfAA() {
        // AmbientBackground tints the ground with the day's accent. However
        // subtle it looks, it moves the ground's luminance, and ink contrast is
        // measured against the *washed* ground rather than the token.
        for weekday in 1...7 {
            for hue in DawnPalette.hueBandSamples(weekday: weekday, steps: 13) {
                for isDark in [false, true] {
                    let accent = DawnPalette.accentColor(hue: hue, dark: isDark)
                    let ground = MercuryColor.surface.resolved(dark: isDark)
                    let washed = accent.composited(opacity: AmbientBackground.maxTintOpacity, over: ground)
                    for (name, ink) in MercuryColor.inkTokensForTesting {
                        let ratio = ColorMath.contrast(ink.resolved(dark: isDark), washed)
                        XCTAssertGreaterThanOrEqual(
                            ratio, ColorMath.aaBody,
                            "\(name) over washed ground [\(isDark ? "dark" : "light")]: \(ratio):1"
                        )
                    }
                }
            }
        }
    }
}
