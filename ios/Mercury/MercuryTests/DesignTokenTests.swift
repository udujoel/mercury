import SwiftUI
import XCTest
@testable import Mercury

/// Guards the parts of the token layer that are structural rather than
/// numeric: that Dynamic Type is actually wired up, that the spacing scale is
/// a scale, that reduce-motion has a real path through it, and that the noise
/// field is the fixed table it claims to be.
final class DesignTokenTests: XCTestCase {

    // MARK: - Typography

    func testEveryTypeTokenScalesWithDynamicType() {
        // The bug this catches: every font in the app was `.system(size:)`,
        // which ignores Dynamic Type completely. A text-style font resolves to
        // a different point size per size category; a fixed-size one does not.
        // Comparing the two extremes is the only way to tell them apart from
        // outside SwiftUI.
        let tokens: [(String, Font)] = [
            ("greeting", MercuryType.greeting),
            ("transcript", MercuryType.transcript),
            ("quote", MercuryType.quote),
            ("cardTitle", MercuryType.cardTitle),
            ("cardBody", MercuryType.cardBody),
            ("chapterTab", MercuryType.chapterTab),
            ("caption", MercuryType.caption),
            ("eyebrow", MercuryType.eyebrow),
            ("timecode", MercuryType.timecode),
            ("figure", MercuryType.figure),
            ("figureCaption", MercuryType.figureCaption),
        ]
        for (name, font) in tokens {
            let small = resolvedPointSize(font, sizeCategory: .extraSmall)
            let huge = resolvedPointSize(font, sizeCategory: .accessibilityExtraExtraExtraLarge)
            XCTAssertGreaterThan(
                huge, small,
                "\(name) did not grow with Dynamic Type — it is probably a fixed .system(size:)"
            )
        }
    }

    func testAFixedSizeFontFailsTheDynamicTypeCheck() {
        // Proves the check above can fail. Without this, a resolver that always
        // reported growth would make every assertion in that test vacuous.
        let fixed = Font.system(size: 40, weight: .light, design: .serif)
        XCTAssertEqual(resolvedPointSize(fixed, sizeCategory: .extraSmall),
                       resolvedPointSize(fixed, sizeCategory: .accessibilityExtraExtraExtraLarge))
    }

    /// Resolves a `Font` to a concrete point size at a given size category.
    private func resolvedPointSize(_ font: Font, sizeCategory: ContentSizeCategory) -> CGFloat {
        let traits = UITraitCollection(preferredContentSizeCategory:
            UIContentSizeCategory(rawValue: sizeCategory.uiKitRawValue))
        return UIFont.preferredFont(from: font, compatibleWith: traits).pointSize
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

// MARK: - Font resolution helper

private extension ContentSizeCategory {
    /// `ContentSizeCategory` and `UIContentSizeCategory` do not bridge.
    var uiKitRawValue: String {
        switch self {
        case .extraSmall: return UIContentSizeCategory.extraSmall.rawValue
        case .accessibilityExtraExtraExtraLarge:
            return UIContentSizeCategory.accessibilityExtraExtraExtraLarge.rawValue
        default: return UIContentSizeCategory.large.rawValue
        }
    }
}

private extension UIFont {
    /// SwiftUI's `Font` is opaque, so there is no supported way to ask it for a
    /// point size. Reflection over its internal provider is the standard
    /// workaround; if a future SDK changes that shape this returns 0 and the
    /// Dynamic Type test fails loudly rather than passing vacuously.
    static func preferredFont(from font: Font, compatibleWith traits: UITraitCollection) -> UIFont {
        guard let style = textStyle(from: font) else {
            // Not a text-style font: resolve the fixed size it was built with.
            return UIFont.systemFont(ofSize: fixedSize(from: font) ?? 0)
        }
        return UIFont.preferredFont(forTextStyle: style, compatibleWith: traits)
    }

    private static func textStyle(from font: Font) -> UIFont.TextStyle? {
        let description = String(describing: font)
        let map: [String: UIFont.TextStyle] = [
            "largeTitle": .largeTitle, "title": .title1, "title2": .title2, "title3": .title3,
            "headline": .headline, "subheadline": .subheadline, "body": .body,
            "callout": .callout, "footnote": .footnote, "caption": .caption1, "caption2": .caption2,
        ]
        // Longest key first so "title2" is not matched by "title".
        for key in map.keys.sorted(by: { $0.count > $1.count }) where description.contains(key) {
            return map[key]
        }
        return nil
    }

    private static func fixedSize(from font: Font) -> CGFloat? {
        let description = String(describing: font)
        guard let range = description.range(of: #"size: ?([0-9.]+)"#, options: .regularExpression) else {
            return nil
        }
        return CGFloat(Double(description[range].split(separator: " ").last ?? "") ?? 0)
    }
}
