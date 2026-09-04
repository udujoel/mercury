import XCTest
@testable import Mercury

/// The point of this file is the first two tests in it.
///
/// `AmbientBackground.derivedHue` used `Hasher()`, which Swift seeds randomly
/// per process. Its own docstring promised "same title always produces the
/// same visual identity"; three consecutive launches of that code with seed
/// `"greeting"` produced hues 0.194, 0.917 and 0.853. A test that only checked
/// self-consistency inside one process would have passed against that bug — so
/// every expectation below is a literal computed by a reference FNV-1a
/// implementation outside Swift and pasted in. A hash carrying per-process
/// state cannot match a literal.
final class DawnPaletteTests: XCTestCase {

    // MARK: - Determinism

    func testFNV1aMatchesTheReferenceImplementation() {
        // FNV-1a 64-bit over UTF-8; offset basis 14695981039346656037,
        // prime 1099511628211. Computed independently of this codebase.
        XCTAssertEqual(DawnPalette.fnv1a(""),          0xCBF2_9CE4_8422_2325)
        XCTAssertEqual(DawnPalette.fnv1a("greeting"),  0xDBDC_244F_A0B5_2AF6)
        XCTAssertEqual(DawnPalette.fnv1a("weather"),   0x3838_E324_75F2_7053)
        XCTAssertEqual(DawnPalette.fnv1a("calendar"),  0x0A7C_F666_EFCB_A93F)
        XCTAssertEqual(DawnPalette.fnv1a("inbox"),     0x961D_E08F_9B26_B75B)
        XCTAssertEqual(DawnPalette.fnv1a("cold_open"), 0xBDB6_701F_1EC4_7678)
        XCTAssertEqual(DawnPalette.fnv1a("briefing"),  0x912D_A2F0_3913_1B19)
    }

    func testHueOffsetIsAFixedValueNotAPerProcessOne() {
        XCTAssertEqual(DawnPalette.hueOffset(seed: "greeting"),  0.064589012622134193, accuracy: 1e-15)
        XCTAssertEqual(DawnPalette.hueOffset(seed: "weather"),  -0.050468754432412909, accuracy: 1e-15)
        XCTAssertEqual(DawnPalette.hueOffset(seed: "calendar"), -0.082625530220427026, accuracy: 1e-15)
        XCTAssertEqual(DawnPalette.hueOffset(seed: "inbox"),     0.015550810156644003, accuracy: 1e-15)
        XCTAssertEqual(DawnPalette.hueOffset(seed: "briefing"),  0.012078469330475333, accuracy: 1e-15)
    }

    func testResolvedAccentIsAFixedColourNotAPerProcessOne() {
        // The end-to-end version of the above: the bytes that actually render.
        let monday = DawnPalette.accent(weekday: 2)
        XCTAssertEqual(monday.light.hexString, "#3C64D6")
        XCTAssertEqual(monday.dark.hexString, "#6D91F5")

        let sunday = DawnPalette.accent(weekday: 1)
        XCTAssertEqual(sunday.light.hexString, "#8B6527")
        XCTAssertEqual(sunday.dark.hexString, "#BF8C35")
    }

    func testHashIsSensitiveToOrderAndToEveryByte() {
        XCTAssertNotEqual(DawnPalette.fnv1a("inbox"), DawnPalette.fnv1a("xobni"))
        XCTAssertNotEqual(DawnPalette.fnv1a("weather"), DawnPalette.fnv1a("weathe"))
        XCTAssertNotEqual(DawnPalette.fnv1a("é"), DawnPalette.fnv1a("e"))
    }

    func testUnitFractionStaysInRange() {
        for seed in ["", "a", "greeting", "cold_open", "🌅", String(repeating: "x", count: 4096)] {
            let u = DawnPalette.unitFraction(seed)
            XCTAssertGreaterThanOrEqual(u, 0.0, "seed \(seed)")
            XCTAssertLessThan(u, 1.0, "seed \(seed)")
        }
    }

    func testHueOffsetNeverLeavesTheBand() {
        for i in 0..<5_000 {
            XCTAssertLessThanOrEqual(abs(DawnPalette.hueOffset(seed: "segment-\(i)")),
                                     DawnPalette.hueBand, "seed segment-\(i)")
        }
    }

    // MARK: - Weekday anchors

    func testSevenAnchorsCoverEveryWeekdayExactlyOnce() {
        XCTAssertEqual(DawnPalette.anchors.count, 7)
        XCTAssertEqual(Set(DawnPalette.anchors.map(\.weekday)), Set(1...7))
    }

    func testWeekdayIndexingFollowsCalendarNotFirstWeekday() throws {
        // Calendar.component(.weekday) is 1 = Sunday regardless of locale.
        // Getting this backwards would ship Sunday's ochre on Monday, and
        // nothing on screen would look broken enough for anyone to notice.
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = try XCTUnwrap(TimeZone(identifier: "UTC"))

        var components = DateComponents()
        components.year = 2026; components.month = 8; components.day = 31  // a Monday
        let date = try XCTUnwrap(gregorian.date(from: components))
        XCTAssertEqual(gregorian.component(.weekday, from: date), 2)
        XCTAssertEqual(DawnPalette.anchor(for: date, calendar: gregorian).name, "Monday")

        // A locale whose week starts on Monday must not shift the mapping.
        var mondayFirst = gregorian
        mondayFirst.firstWeekday = 2
        XCTAssertEqual(DawnPalette.anchor(for: date, calendar: mondayFirst).name, "Monday")

        // And the day either side, so an off-by-one cannot hide.
        let sunday = try XCTUnwrap(gregorian.date(byAdding: .day, value: -1, to: date))
        let tuesday = try XCTUnwrap(gregorian.date(byAdding: .day, value: 1, to: date))
        XCTAssertEqual(DawnPalette.anchor(for: sunday, calendar: gregorian).name, "Sunday")
        XCTAssertEqual(DawnPalette.anchor(for: tuesday, calendar: gregorian).name, "Tuesday")
    }

    func testAnchorHuesWalkColdToWarmMondayThroughSunday() {
        // A continuous walk round the wheel, not seven unrelated hues.
        let order = [2, 3, 4, 5, 6, 7, 1]  // Mon … Sun
        var previous = DawnPalette.anchor(weekday: order[0]).hue
        for weekday in order.dropFirst() {
            let hue = DawnPalette.anchor(weekday: weekday).hue
            let step = ColorMath.wrapHue(hue - previous)
            XCTAssertGreaterThan(step, 0.0, "weekday \(weekday) did not advance")
            XCTAssertLessThan(step, 0.20, "weekday \(weekday) jumped too far to read as the same family")
            previous = hue
        }
    }

    func testUnknownWeekdayFallsBackRatherThanCrashing() {
        // `Calendar` cannot produce these, but the API takes an `Int` and a
        // caller doing arithmetic on a weekday can.
        XCTAssertEqual(DawnPalette.anchor(weekday: 0).name, "Monday")
        XCTAssertEqual(DawnPalette.anchor(weekday: 99).name, "Monday")
    }

    // MARK: - Accents

    func testSameDayAndSeedAlwaysResolveToTheSameAccent() {
        XCTAssertEqual(DawnPalette.accent(weekday: 2, seed: "cold_open").light,
                       DawnPalette.accent(weekday: 2, seed: "cold_open").light)
    }

    func testTwoMondaysAreTheSameColourAndTuesdayIsNot() {
        let monday = DawnPalette.accent(weekday: 2, seed: "greeting").light
        XCTAssertEqual(DawnPalette.accent(weekday: 2, seed: "greeting").light, monday)
        XCTAssertNotEqual(DawnPalette.accent(weekday: 3, seed: "greeting").light, monday)
    }

    func testTwoSurfacesOnTheSameDayDifferButStayRelated() {
        let greeting = DawnPalette.accent(weekday: 4, seed: "greeting").light
        let inbox = DawnPalette.accent(weekday: 4, seed: "inbox").light
        XCTAssertNotEqual(greeting, inbox)
        // Same morning: both within the band of the day's anchor.
        for seed in ["greeting", "inbox"] {
            XCTAssertLessThanOrEqual(abs(DawnPalette.hueOffset(seed: seed)), DawnPalette.hueBand)
        }
    }

    func testEveryWeekdayProducesADistinctAccent() {
        XCTAssertEqual(Set((1...7).map { DawnPalette.accent(weekday: $0).light.hexString }).count, 7)
    }

    func testAccentsDifferBetweenAppearances() {
        // A single value cannot clear AA on paper and on night; if these ever
        // match, the adaptive layer has collapsed to one appearance again.
        for weekday in 1...7 {
            let accent = DawnPalette.accent(weekday: weekday)
            XCTAssertNotEqual(accent.light, accent.dark, "weekday \(weekday)")
            XCTAssertLessThan(accent.light.relativeLuminance, accent.dark.relativeLuminance)
        }
    }

    // MARK: - Construction

    func testAccentsLandOnTheirTargetLuminance() {
        for weekday in 1...7 {
            for hue in DawnPalette.hueBandSamples(weekday: weekday, steps: 9) {
                XCTAssertEqual(DawnPalette.accentColor(hue: hue, dark: false).relativeLuminance,
                               DawnPalette.lightAccentLuminance, accuracy: 1e-4)
                XCTAssertEqual(DawnPalette.accentColor(hue: hue, dark: true).relativeLuminance,
                               DawnPalette.darkAccentLuminance, accuracy: 1e-4)
            }
        }
    }

    func testAccentsStayInGamutAndReadAsColourNotGrey() {
        for weekday in 1...7 {
            for isDark in [false, true] {
                let c = DawnPalette.accentColor(hue: DawnPalette.anchor(weekday: weekday).hue, dark: isDark)
                for channel in [c.red, c.green, c.blue] {
                    XCTAssertGreaterThanOrEqual(channel, 0.0)
                    XCTAssertLessThanOrEqual(channel, 1.0)
                }
                // Chroma has to be visible, or the "one morning one colour"
                // premise renders as seven greys.
                XCTAssertGreaterThan(max(c.red, c.green, c.blue) - min(c.red, c.green, c.blue), 0.15,
                                     "weekday \(weekday) dark=\(isDark) is too close to grey")
            }
        }
    }
}
