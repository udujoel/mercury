import SwiftUI

/// Card surfaces: fill, rim and elevation.
///
/// The cards this replaces were opaque fills carrying
/// `.shadow(color: .black.opacity(0.5), radius: 20, y: 12)` — a black shadow on
/// a black canvas, which is invisible. The separation people thought they were
/// seeing came entirely from the saturated gradient inside each card, so the
/// shadow was cost with no effect.
///
/// In the editorial register separation has to be real, because a white card on
/// off-white paper has almost no luminance difference to lean on. Three devices
/// do the work, in this order of importance: a hairline rim, which reads at any
/// size; a soft shadow tuned per appearance, since a dark-mode shadow on a
/// near-black ground is as invisible as the old one; and a small luminance step
/// between `surface` and `surfaceRaised`.
enum MercurySurface {

    /// How far a surface sits above the page.
    enum Elevation {
        /// Flush with the page — a section that is grouped but not lifted.
        case flat
        /// A card in a list.
        case raised
        /// Something temporarily above everything: a sheet, a drag in progress.
        case floating
    }

    struct Shadow {
        let color: AdaptiveColor
        let opacity: AdaptiveOpacity
        let radius: CGFloat
        let y: CGFloat
    }

    /// Shadows need different alphas per appearance. On paper a shadow is a
    /// grey that darkens the page; on a near-black ground darkening does
    /// nothing, so dark mode leans on the rim instead and keeps only enough
    /// shadow to seat the card.
    struct AdaptiveOpacity {
        let light: Double
        let dark: Double
        func resolved(dark isDark: Bool) -> Double { isDark ? dark : light }
    }

    static func shadow(_ elevation: Elevation) -> Shadow? {
        switch elevation {
        case .flat:
            return nil
        case .raised:
            return Shadow(color: AdaptiveColor(light: "1A1A22", dark: "000000"),
                          opacity: AdaptiveOpacity(light: 0.07, dark: 0.34),
                          radius: 10, y: 3)
        case .floating:
            return Shadow(color: AdaptiveColor(light: "1A1A22", dark: "000000"),
                          opacity: AdaptiveOpacity(light: 0.13, dark: 0.48),
                          radius: 26, y: 12)
        }
    }

    /// The fill for an elevation.
    ///
    /// `.flat` is documented as flush with the page, and the fill was an
    /// unconditional `surfaceRaised` that contradicted it: a flat section
    /// carried the full luminance step, so "grouped but not lifted" rendered as
    /// lifted and the elevation argument only ever chose a shadow. Elevation
    /// now picks the ground as well as the shadow, which is what makes `.flat`
    /// mean anything.
    static func fill(_ elevation: Elevation) -> AdaptiveColor {
        switch elevation {
        case .flat:                return MercuryColor.surface
        case .raised, .floating:   return MercuryColor.surfaceRaised
        }
    }

    /// How much of the page's wash a card gets. Enough that the day's colour is
    /// present, little enough that the card does not become a swatch.
    static let tintIntensity = 0.55

    /// The wash a tinted card draws.
    ///
    /// The modifier below renders exactly this value and the tests measure
    /// exactly this value, so the ground asserted in `ContrastTests` is the
    /// ground on screen rather than a second model of it.
    static func wash(elevation: Elevation, tintSeed: String, date: Date = Date()) -> AmbientBackground {
        AmbientBackground(seed: tintSeed,
                          intensity: tintIntensity,
                          date: date,
                          base: fill(elevation))
    }

    /// The rim. Does the real separating work, and is the only one of the three
    /// that survives at every size and in every appearance.
    static let rimWidth: CGFloat = 1.0 / 3.0 * 2  // ~0.67pt: a hairline at 3x, visible at 2x

    static let cornerRadius = MercuryColor.cardCornerRadius
}

private struct MercuryCardSurface: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme

    let elevation: MercurySurface.Elevation
    let tintSeed: String?

    private var isDark: Bool { colorScheme == .dark }

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: MercurySurface.cornerRadius, style: .continuous)
        return content
            .background {
                Group {
                    if let tintSeed {
                        // The wash carries its own ground — this card's
                        // elevation fill — rather than being stacked over one.
                        // Stacking meant the wash's opaque page surface painted
                        // the elevation step away.
                        MercurySurface.wash(elevation: elevation, tintSeed: tintSeed)
                    } else {
                        MercurySurface.fill(elevation).color
                    }
                }
                .clipShape(shape)
            }
            .overlay {
                shape.strokeBorder(MercuryColor.hairline.color, lineWidth: MercurySurface.rimWidth)
            }
            .modifier(MercuryShadow(shadow: MercurySurface.shadow(elevation), isDark: isDark))
            .contentShape(shape)
    }
}

private struct MercuryShadow: ViewModifier {
    let shadow: MercurySurface.Shadow?
    let isDark: Bool

    func body(content: Content) -> some View {
        if let shadow {
            content.shadow(
                color: shadow.color.resolved(dark: isDark).color
                    .opacity(shadow.opacity.resolved(dark: isDark)),
                radius: shadow.radius,
                y: shadow.y
            )
        } else {
            content
        }
    }
}

extension View {
    /// Standard card treatment: raised fill, hairline rim, elevation shadow.
    ///
    /// Pass `tintSeed` to carry the day's colour into the card at reduced
    /// strength; omit it for a card that should stay plain paper.
    func mercuryCard(_ elevation: MercurySurface.Elevation = .raised,
                     tintSeed: String? = nil) -> some View {
        modifier(MercuryCardSurface(elevation: elevation, tintSeed: tintSeed))
    }
}
