import SwiftUI

/// Liquid Glass, and the one place Mercury decides where it goes.
///
/// ADR-012 put the app on paper, and paper is content. Glass is a material for
/// the layer that floats ABOVE content, meaning controls and chrome, and it
/// only reads as glass while there is something underneath for it to bend.
/// So the rule here is narrow on purpose: cards, rows, the transcript and the
/// accent-filled buttons stay ink on paper, and glass goes on the few
/// surfaces that genuinely float over a scrolling page.
///
/// Most of the chrome needs nothing from this file. Navigation bars, toolbar
/// buttons and the back button adopt glass on their own once the app is built
/// against the iOS 26 SDK, and nothing in the app overrode them (no
/// `toolbarBackground`, no appearance proxies), so there was nothing to strip
/// before they could.
///
/// The accent buttons are deliberately not glass. Their contrast is pinned:
/// `inkOnAccent` is measured against the solid accent in `ContrastTests`.
/// Tinted glass over off-white paper would render lighter than that fill, so
/// swapping it in would quietly move a measured ground to an unmeasured one.
private struct MercuryGlassSurface<S: InsettableShape>: ViewModifier {
    let shape: S
    let isInteractive: Bool

    /// Guarded at compile time as well as run time. `#available` is a
    /// runtime check, and the symbol still has to exist in the SDK being
    /// built against, so this keeps building on a pre-26 toolchain, per
    /// ios.yml's note on `#if compiler`. Swift 6.2 is Xcode 26's.
    func body(content: Content) -> some View {
        Group {
            #if compiler(>=6.2)
            if #available(iOS 26.0, *) {
                // `.regular`, never `.clear`: `.clear` is for small controls
                // over photographs and video, and it does not keep its own
                // text legible. No shadow or rim of ours on this branch,
                // because the system draws the edge and adapts it for Reduce
                // Transparency and Increase Contrast, which a hand-drawn
                // stroke would not.
                content.glassEffect(.regular.interactive(isInteractive), in: shape)
            } else {
                content.modifier(LegacyGlass(shape: shape))
            }
            #else
            content.modifier(LegacyGlass(shape: shape))
            #endif
        }
        // Once, outside every branch, so no future branch can leave the
        // glass untappable outside its label.
        .contentShape(shape)
    }
}

/// iOS 17 to 25, where there is no glass: a system material in the same
/// shape, plus the hairline rim every other Mercury surface uses to separate
/// itself from the page. A material rather than an imitation of glass,
/// because an imitation would be the thing to delete when the floor rises.
private struct LegacyGlass<S: InsettableShape>: ViewModifier {
    let shape: S

    func body(content: Content) -> some View {
        content
            .background { shape.fill(.regularMaterial) }
            .overlay {
                shape.strokeBorder(MercuryColor.hairline.color,
                                   lineWidth: MercurySurface.rimWidth)
            }
    }
}

extension View {
    /// A floating glass surface in `shape`, with a material fallback below
    /// iOS 26.
    ///
    /// Apply it after padding and frames, so the glass is sized from the
    /// finished layout. Pass `interactive` only when the surface itself
    /// responds to a touch. It adds press feedback, and on a surface that
    /// does nothing that feedback invites taps that go nowhere. The whole
    /// shape is made the hit area, as `mercuryCard` does, because glass draws
    /// the shape without making it tappable.
    func mercuryGlass<S: InsettableShape>(in shape: S, interactive: Bool = false) -> some View {
        modifier(MercuryGlassSurface(shape: shape, isInteractive: interactive))
    }
}

/// A button drawn as a floating glass capsule.
///
/// The style applies the glass itself rather than leaving it to the label,
/// so the two cannot be mismatched. `.plain` dims its label while pressed,
/// and partial opacity on glass, or on anything containing it, stops it
/// refracting, so a plain-styled button with glass in its label would go
/// flat every time it was touched. On iOS 26 nothing is dimmed and the
/// interactive glass gives the press feedback. Below it there is no glass to
/// protect and no interactive response, so the button dims the way `.plain`
/// did and a press still shows.
///
/// Give the label its padding and frames; the capsule is sized from them.
struct MercuryGlassCapsuleButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .mercuryGlass(in: Capsule(), interactive: true)
            .modifier(LegacyPressDim(isPressed: configuration.isPressed))
    }
}

private struct LegacyPressDim: ViewModifier {
    let isPressed: Bool

    /// A plain value rather than a second `#available` branch, so the floor
    /// lives in `MercuryGlass.isAvailable` alone and this cannot disagree
    /// with the surface about which side of it the device is on.
    func body(content: Content) -> some View {
        content.opacity(isPressed && !MercuryGlass.isAvailable ? 0.7 : 1)
    }
}

/// Whether this device draws real glass.
///
/// For decisions that only need to know, not to call a glass API. Anything
/// that calls one still needs its own `#available`, because the compiler
/// checks availability by scope, not by the value of a Bool.
enum MercuryGlass {
    static var isAvailable: Bool {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) { return true }
        #endif
        return false
    }
}

extension ButtonStyle where Self == MercuryGlassCapsuleButtonStyle {
    static var mercuryGlassCapsule: MercuryGlassCapsuleButtonStyle {
        MercuryGlassCapsuleButtonStyle()
    }
}
