import SwiftUI
import XCTest
@testable import Mercury

/// Guards the parts of the token layer that are structural rather than
/// numeric: that Dynamic Type is actually wired up, that the spacing scale is
/// a scale, that reduce-motion has a real path through it, and that the noise
/// field is the fixed table it claims to be.
final class DesignTokenTests: XCTestCase {

    // MARK: - Typography

    func testEveryTypeTokenIsBuiltFromATextStyleSoDynamicTypeApplies() {
        // The bug this catches: every font in the app was `.system(size:)`,
        // which ignores Dynamic Type completely — an app whose whole purpose is
        // reading things to you shipped type that would not grow for someone
        // who needs it larger.
        //
        // `Font` is opaque, so the check is equality against a font rebuilt
        // from a text style. A fixed-size font is never equal to a text-style
        // one, which is precisely the regression worth catching.
        for token in MercuryType.dynamicTypeContract {
            var expected = Font.system(token.style, design: token.design, weight: token.weight)
            if token.tabular { expected = expected.monospacedDigit() }
            XCTAssertEqual(
                token.font, expected,
                "\(token.name) is not the text-style font it claims to be — "
                + "if it was changed to .system(size:), Dynamic Type stopped working for it"
            )
        }
    }

    func testAFixedSizeFontWouldFailThatCheck() {
        // Proves the assertion above can fail. Without this, a token that was
        // silently reverted to a point size might still slip through if `Font`
        // equality were looser than assumed.
        XCTAssertNotEqual(Font.system(size: 40, weight: .light, design: .serif),
                          Font.system(.largeTitle, design: .serif, weight: .light))
        XCTAssertNotEqual(Font.system(size: 26, weight: .medium),
                          Font.system(.title2, design: .default, weight: .medium))
    }

    func testTextStylesActuallyScaleOnThisPlatform() {
        // The premise behind the test above: a text style resolves to a
        // different point size per size category. Checked directly through
        // UIKit so the contract is verified rather than assumed.
        for token in MercuryType.dynamicTypeContract {
            let style = UIFont.TextStyle(token.style)
            let small = UIFont.preferredFont(
                forTextStyle: style,
                compatibleWith: UITraitCollection(preferredContentSizeCategory: .extraSmall))
            let huge = UIFont.preferredFont(
                forTextStyle: style,
                compatibleWith: UITraitCollection(preferredContentSizeCategory: .accessibilityExtraExtraExtraLarge))
            XCTAssertGreaterThan(huge.pointSize, small.pointSize, "\(token.name) (\(style.rawValue))")
        }
    }

    func testSerifIsTheVoiceAndSansIsTheInterface() {
        // The one rule tasks 2, 3 and 7 have to follow: anything a host says is
        // set in the serif, anything the app says is set in SF.
        for token in MercuryType.dynamicTypeContract {
            let isVoice = MercuryType.voiceTokenNames.contains(token.name)
            XCTAssertEqual(token.design, isVoice ? .serif : .default,
                           "\(token.name) is on the wrong side of the serif/sans rule")
        }
        XCTAssertEqual(MercuryType.voiceTokenNames, ["greeting", "transcript", "quote"])
    }

    func testDigitsThatLineUpUseTabularFigures() {
        // Proportional digits jitter as numbers change, which is wrong next to
        // a progress bar and wrong in a column.
        let tabular = Set(MercuryType.dynamicTypeContract.filter(\.tabular).map(\.name))
        XCTAssertEqual(tabular, ["timecode", "figure", "figureCaption"])
        for token in MercuryType.dynamicTypeContract where token.tabular {
            XCTAssertNotEqual(token.font,
                              Font.system(token.style, design: token.design, weight: token.weight),
                              "\(token.name) claims tabular figures but is the plain font")
        }
    }

    // MARK: - Spacing

    func testSpacingScaleIsStrictlyAscending() {
        for (a, b) in zip(MercurySpace.scale, MercurySpace.scale.dropFirst()) {
            XCTAssertLessThan(a, b, "the spacing scale must ascend")
        }
    }

    func testSpacingStepsSitOnTheFourPointGrid() {
        // `hair` is the deliberate exception: 2pt optical nudges.
        for step in MercurySpace.scale where step != MercurySpace.hair {
            XCTAssertEqual(step.truncatingRemainder(dividingBy: 4), 0,
                           "\(step) is off the four-point grid")
        }
    }

    func testTheGutterIsTheValueThePlayerWasHardcoding() {
        // PlayerView, ChapterTabs and TransportControls each wrote `20` for
        // this. Tasks 2 and 3 replace those with `MercurySpace.gutter`.
        XCTAssertEqual(MercurySpace.gutter, 20)
    }

    // MARK: - Motion

    func testReduceMotionReplacesMovementWithAFade() {
        let moving = [MercuryMotion.tap, MercuryMotion.reposition, MercuryMotion.present]
        for animation in moving {
            XCTAssertEqual(MercuryMotion.resolved(animation, reduceMotion: true),
                           MercuryMotion.reducedSubstitute)
            XCTAssertEqual(MercuryMotion.resolved(animation, reduceMotion: false), animation)
        }
    }

    func testReduceMotionKeepsTheCrossfadeBecauseItDoesNotMove() {
        // A cross-fade is already motion-free. Stripping it would make the
        // player cut between sentences, which PRD 3.4 rules out and which
        // reduce-motion never asked for.
        XCTAssertEqual(MercuryMotion.resolved(MercuryMotion.crossfade, reduceMotion: true),
                       MercuryMotion.crossfade)
    }

    func testReduceMotionStopsContinuousAmbientMotionEntirely() {
        XCTAssertNil(MercuryMotion.resolved(MercuryMotion.ambient, reduceMotion: true))
        XCTAssertNotNil(MercuryMotion.resolved(MercuryMotion.ambient, reduceMotion: false))
    }

    func testMotionContextExposesBothDecisions() {
        let on = MercuryMotionContext(reduceMotion: true)
        XCTAssertFalse(on.allowsAmbientMotion)
        XCTAssertEqual(on.resolve(MercuryMotion.tap), MercuryMotion.reducedSubstitute)

        let off = MercuryMotionContext(reduceMotion: false)
        XCTAssertTrue(off.allowsAmbientMotion)
        XCTAssertEqual(off.resolve(MercuryMotion.tap), MercuryMotion.tap)
    }

    // MARK: - Noise

    func testSplitMix64IsDeterministicAcrossInstances() {
        var a = SplitMix64(seed: 0x9E37_79B9_7F4A_7C15)
        var b = SplitMix64(seed: 0x9E37_79B9_7F4A_7C15)
        for _ in 0..<64 { XCTAssertEqual(a.next(), b.next()) }
    }

    func testSplitMix64MatchesItsReferenceOutput() {
        // Literals, for the same reason DawnPaletteTests uses literals: a
        // generator carrying process state cannot match a fixed sequence.
        var generator = SplitMix64(seed: 0)
        XCTAssertEqual(generator.next(), 0xE220_A839_7B1D_CDAF)
        XCTAssertEqual(generator.next(), 0x6E78_9E6A_A1B9_65F4)
        XCTAssertEqual(generator.next(), 0x06C4_5D18_8009_454F)
    }

    func testSplitMix64UnitStaysInRange() {
        var generator = SplitMix64(seed: 12345)
        for _ in 0..<10_000 {
            let u = generator.nextUnit()
            XCTAssertGreaterThanOrEqual(u, 0.0)
            XCTAssertLessThan(u, 1.0)
        }
    }

    func testNoiseFieldIsAFixedSizeTableNotAreaScaled() {
        // The old overlay drew width * height / 40 ellipses — roughly 5,850 for
        // one full-width card, growing with area, on every redraw. A constant
        // table is the fix; this pins the constant.
        XCTAssertEqual(NoiseOverlay.pointCount, 900)
    }

    func testNoiseOffsetIsDeterministicPerSeedAndDiffersBetweenSeeds() {
        // Two surfaces on screen must not share a field, or the pattern reads.
        XCTAssertEqual(DawnPalette.fnv1a("greeting"), DawnPalette.fnv1a("greeting"))
        XCTAssertNotEqual(DawnPalette.fnv1a("greeting"), DawnPalette.fnv1a("inbox"))
    }

    // MARK: - Surfaces

    func testShadowsAreTunedPerAppearanceRatherThanShared() {
        // The old card used .black.opacity(0.5) on a black canvas — invisible.
        // A dark-mode shadow has to be much stronger than a light-mode one to
        // register at all, so a single value cannot serve both.
        let raised = MercurySurface.shadow(.raised)
        XCTAssertNotNil(raised)
        XCTAssertGreaterThan(raised!.opacity.dark, raised!.opacity.light)

        let floating = MercurySurface.shadow(.floating)
        XCTAssertNotNil(floating)
        XCTAssertGreaterThan(floating!.radius, raised!.radius)
        XCTAssertGreaterThan(floating!.opacity.light, raised!.opacity.light)

        XCTAssertNil(MercurySurface.shadow(.flat))
    }

    func testTheRimIsAHairlineNotABorder() {
        XCTAssertGreaterThan(MercurySurface.rimWidth, 0)
        XCTAssertLessThanOrEqual(MercurySurface.rimWidth, 1.0)
    }
}

private extension UIFont.TextStyle {
    /// `Font.TextStyle` and `UIFont.TextStyle` describe the same eleven styles
    /// but do not bridge, so the mapping is written out once here.
    init(_ style: Font.TextStyle) {
        switch style {
        case .largeTitle:  self = .largeTitle
        case .title:       self = .title1
        case .title2:      self = .title2
        case .title3:      self = .title3
        case .headline:    self = .headline
        case .subheadline: self = .subheadline
        case .body:        self = .body
        case .callout:     self = .callout
        case .footnote:    self = .footnote
        case .caption:     self = .caption1
        case .caption2:    self = .caption2
        @unknown default:  self = .body
        }
    }
}
