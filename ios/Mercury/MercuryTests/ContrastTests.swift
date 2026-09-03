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
        XCTAssertEqual(ratio(MercuryColor.inkFaint, MercuryColor.surface, dark: false), 5.64, accuracy: 0.02)
        XCTAssertEqual(ratio(MercuryColor.inkFaint, MercuryColor.surface, dark: true), 6.12, accuracy: 0.02)
    }

    func testTheDraftInkFaintValuesWouldHaveFailed() {
        // Explicit regression on both earlier drafts. If someone lightens
        // inkFaint back toward either of them for delicacy, this states the
        // cost in the units that matter.
        //
        // First draft: under AA even on bare paper.
        XCTAssertLessThan(ColorMath.contrast(RGB(hex: "8A8B96"), MercuryColor.surfaceRaised.light),
                          ColorMath.aaBody)
        XCTAssertLessThan(ColorMath.contrast(RGB(hex: "77777F"), MercuryColor.surfaceRaised.dark),
                          ColorMath.aaBody)

        // Second draft: cleared AA unwashed, then failed once the ambient tint
        // was drawn under it — which is the case that actually ships.
        let secondDraft = RGB(hex: "6C6D76")
        XCTAssertGreaterThanOrEqual(
            ColorMath.contrast(secondDraft, MercuryColor.surface.light), ColorMath.aaBody)
        let washed = DawnPalette.accentColor(hue: DawnPalette.anchor(weekday: 2).hue, dark: false)
            .composited(opacity: AmbientBackground.maxTintOpacity, over: MercuryColor.surface.light)
        XCTAssertLessThan(ColorMath.contrast(secondDraft, washed), ColorMath.aaBody)

        // What shipped survives both.
        XCTAssertGreaterThanOrEqual(
            ColorMath.contrast(MercuryColor.inkFaint.light, washed), ColorMath.aaBody)
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

    /// The test above measures a ground it builds itself. This one measures the
    /// ground the *view* produces, from the view's own stored properties, which
    /// is the gap the `dark: false` bug lived in: the arithmetic above was
    /// right and the renderer disagreed with it.
    func testInkClearsAAOverTheGroundTheViewItselfComputes() {
        for isDark in [false, true] {
            for family in [AmbientBackground.Family.derived, .greeting, .weather, .calendar, .inbox] {
                let page = AmbientBackground(seed: "briefing", family: family)
                let card = MercurySurface.wash(elevation: .raised, tintSeed: "briefing")
                for ground in [page.ground(dark: isDark), card.ground(dark: isDark)] {
                    for (name, ink) in MercuryColor.inkTokensForTesting {
                        let ratio = ColorMath.contrast(ink.resolved(dark: isDark), ground)
                        XCTAssertGreaterThanOrEqual(
                            ratio, ColorMath.aaBody,
                            "\(name) on \(family) ground \(ground.hexString) "
                            + "[\(isDark ? "dark" : "light")]: \(ratio):1")
                    }
                }
            }
        }
    }

    // MARK: - The wash resolves the appearance it is drawn in

    func testTheWashUsesTheAppearancesOwnAccentRatherThanAlwaysTheLightOne() {
        // The accent is pinned to a different luminance per appearance — 0.150
        // on paper, 0.300 on night — so "the light accent in dark mode" is not
        // a near-miss, it is a colour solved against the wrong ground.
        for weekday in 1...7 {
            let date = Self.date(weekday: weekday)
            let light = AmbientBackground.accent(seed: "greeting", date: date, family: .greeting, dark: false)
            let dark = AmbientBackground.accent(seed: "greeting", date: date, family: .greeting, dark: true)

            XCTAssertEqual(light.relativeLuminance, DawnPalette.lightAccentLuminance, accuracy: 1e-4)
            XCTAssertEqual(dark.relativeLuminance, DawnPalette.darkAccentLuminance, accuracy: 1e-4)
            XCTAssertNotEqual(light, dark)
        }
    }

    func testTheHardcodedLightAccentMeasurablyChangedTheDarkGround() {
        // The bug, stated as a number rather than as a description. Washing the
        // dark page with the light accent lands on a different ground than
        // washing it with the dark accent — the light accent carries barely
        // half the luminance the dark target asks for, so the dark wash it
        // rendered was roughly half the strength the tokens were tuned against.
        let date = Self.date(weekday: 2)
        let correct = AmbientBackground(seed: "greeting", date: date, family: .greeting).ground(dark: true)
        let buggy = AmbientBackground.washedGround(
            base: MercuryColor.surface.dark,
            accent: AmbientBackground.accent(seed: "greeting", date: date, family: .greeting, dark: false),
            intensity: 1.0)

        XCTAssertNotEqual(correct, buggy)
        XCTAssertGreaterThan(correct.relativeLuminance, buggy.relativeLuminance,
                             "the dark accent is the brighter of the two, so the correct wash lifts "
                             + "the near-black page further than the light accent did")
        // Both still clear AA — this was a fidelity bug, not a legibility one,
        // and saying so is more useful than implying the app was unreadable.
        for ground in [correct, buggy] {
            XCTAssertGreaterThanOrEqual(
                ColorMath.contrast(MercuryColor.inkFaint.dark, ground), ColorMath.aaBody)
        }
    }

    // MARK: - Elevation

    func testATintedCardKeepsTheElevationStepItsUntintedNeighbourHas() {
        // The defect: `AmbientBackground` opened with an opaque
        // `MercuryColor.surface`, so a card that asked for a tint had its
        // `surfaceRaised` fill painted over with page paper. Two cards in one
        // list, one tinted and one not, sat on different grounds — and the
        // tinted one sat on the page, which is the opposite of raised.
        // Note the metric. "Lighter than the page" is *not* the property: on
        // light the accent is darker than paper, so the wash pulls a white card
        // below page luminance and the card ends up slightly darker than the
        // page — separation there comes from the rim and the shadow. What must
        // survive is the elevation *fill*: a tinted card has to be built from
        // the ground its elevation asked for, not from the page.
        for isDark in [false, true] {
            let page = MercuryColor.surface.resolved(dark: isDark)
            let raised = MercurySurface.fill(.raised).resolved(dark: isDark)
            let plainStep = abs(raised.relativeLuminance - page.relativeLuminance)
            XCTAssertGreaterThan(plainStep, 0, "surfaceRaised must differ from surface at all")

            let tinted = MercurySurface.wash(elevation: .raised, tintSeed: "briefing").ground(dark: isDark)

            // The pre-fix ground: the identical wash, but composited over the
            // page because `AmbientBackground` insisted on painting its own.
            let regressed = AmbientBackground.washedGround(
                base: page,
                accent: AmbientBackground.accent(seed: "briefing", date: Date(),
                                                 family: .derived, dark: isDark),
                intensity: MercurySurface.tintIntensity)

            XCTAssertNotEqual(tinted, regressed,
                              "the tinted card must not resolve to the page's washed ground")
            let survivingStep = abs(tinted.relativeLuminance - regressed.relativeLuminance)
            XCTAssertGreaterThan(
                survivingStep, plainStep * 0.8,
                "the wash is a few percent alpha, so nearly all of the elevation step should "
                + "survive it [\(isDark ? "dark" : "light")]: plain \(plainStep), surviving \(survivingStep)")
        }
    }

    func testFlatIsFlushWithThePageAndRaisedIsNot() {
        // `.flat` is documented as "flush with the page — a section that is
        // grouped but not lifted". The fill ignored elevation entirely, so a
        // flat section still carried the raised luminance step.
        XCTAssertEqual(MercurySurface.fill(.flat).light, MercuryColor.surface.light)
        XCTAssertEqual(MercurySurface.fill(.flat).dark, MercuryColor.surface.dark)
        for elevation in [MercurySurface.Elevation.raised, .floating] {
            XCTAssertEqual(MercurySurface.fill(elevation).light, MercuryColor.surfaceRaised.light)
            XCTAssertEqual(MercurySurface.fill(elevation).dark, MercuryColor.surfaceRaised.dark)
        }
        for isDark in [false, true] {
            XCTAssertEqual(MercurySurface.fill(.flat).resolved(dark: isDark),
                           MercuryColor.surface.resolved(dark: isDark))
            XCTAssertNotEqual(MercurySurface.fill(.raised).resolved(dark: isDark),
                              MercurySurface.fill(.flat).resolved(dark: isDark))
        }
    }

    /// Seven consecutive days, so every weekday anchor is exercised. Which
    /// index lands on which weekday depends on the runner's calendar and does
    /// not matter here: the assertions hold for every anchor, which is the
    /// property being checked.
    private static func date(weekday: Int) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let start = calendar.date(from: DateComponents(year: 2026, month: 8, day: 30))!
        return calendar.date(byAdding: .day, value: weekday - 1, to: start)!
    }
}
