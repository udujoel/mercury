import SwiftUI

/// Motion tokens, and one documented place to ask about reduce-motion.
///
/// The app animates in several places and asks the system about Reduce Motion
/// in none of them. That setting is not a preference about taste: for a user
/// with vestibular sensitivity, a card that scales and slides is a symptom
/// trigger. The rule here is the accessible one — honour the *intent* of the
/// animation while removing the movement — rather than the lazy one, which is
/// to disable animation entirely and leave the interface cutting between states
/// with no continuity at all.
enum MercuryMotion {

    /// Sentence-to-sentence cross-fade in the player. PRD 3.4 specified 220ms
    /// and "never a hard cut"; that number survives ADR-012 unchanged, because
    /// it was about the reading experience rather than the visual register.
    static let crossfade = Animation.easeInOut(duration: 0.22)

    /// A control acknowledging a tap.
    static let tap = Animation.spring(response: 0.30, dampingFraction: 0.82)

    /// A card or row moving to a new position.
    static let reposition = Animation.spring(response: 0.42, dampingFraction: 0.86)

    /// Something arriving or leaving — a sheet, a newly ready briefing.
    static let present = Animation.spring(response: 0.50, dampingFraction: 0.88)

    /// Continuous, non-interactive motion: the waveform pill while audio plays.
    static let ambient = Animation.easeInOut(duration: 1.4).repeatForever(autoreverses: true)

    /// The fade used in place of movement when Reduce Motion is on. Short
    /// enough not to feel like a delay, long enough to read as a transition.
    static let reducedSubstitute = Animation.easeInOut(duration: 0.18)

    /// The animation to actually use, given the user's setting.
    ///
    /// Springs and slides become a plain fade; a cross-fade is already
    /// motion-free and passes through unchanged. Ambient loops stop entirely —
    /// a thing that moves forever with no user intent behind it is exactly what
    /// the setting exists to switch off.
    static func resolved(_ animation: Animation, reduceMotion: Bool) -> Animation? {
        guard reduceMotion else { return animation }
        if animation == ambient { return nil }
        if animation == crossfade { return crossfade }
        return reducedSubstitute
    }
}

/// Reads the accessibility setting so a view does not have to.
///
///     @Environment(\.mercuryMotion) private var motion
///     withAnimation(motion.resolve(MercuryMotion.reposition)) { … }
///
/// `resolve` returns `Animation?`; a `nil` means "apply this change without
/// animating", which `withAnimation` handles correctly.
struct MercuryMotionContext {
    var reduceMotion: Bool = false

    func resolve(_ animation: Animation) -> Animation? {
        MercuryMotion.resolved(animation, reduceMotion: reduceMotion)
    }

    /// Whether continuous decorative motion should run at all.
    var allowsAmbientMotion: Bool { !reduceMotion }
}

private struct MercuryMotionKey: EnvironmentKey {
    static let defaultValue = MercuryMotionContext()
}

extension EnvironmentValues {
    var mercuryMotion: MercuryMotionContext {
        get { self[MercuryMotionKey.self] }
        set { self[MercuryMotionKey.self] = newValue }
    }
}

extension View {
    /// Bridges the system setting into `\.mercuryMotion`. Apply once, at the
    /// root — `MercuryApp`'s `WindowGroup` — so every screen below inherits it.
    func mercuryMotionEnvironment(reduceMotion: Bool) -> some View {
        environment(\.mercuryMotion, MercuryMotionContext(reduceMotion: reduceMotion))
    }
}
