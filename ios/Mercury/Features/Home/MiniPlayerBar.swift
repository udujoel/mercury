import SwiftUI

/// The bar pinned to the bottom of Home while a briefing is loaded.
///
/// The bug it fixes: leaving the player restarted the briefing. Not because
/// playback stopped — `PlaybackCoordinator` keeps going, and the audio
/// session survives the screen — but because the only way back in was the
/// greeting card, and that card called `AppState.playBriefing()`, which
/// re-fetches, re-downloads and calls `playback.load(…)`, resetting position
/// to zero. Huxe's rule is that playback survives leaving the screen; the
/// coordinator already honoured it and the navigation did not.
///
/// So this bar does not play anything. It shows what is playing and taps
/// through to the player.
struct MiniPlayerBar: View {
    @Environment(\.mercuryMotion) private var motion

    let manifest: BriefingManifest
    let playback: PlaybackCoordinator
    let onOpen: () -> Void

    private var progress: Double {
        PlayerTransport.progress(positionMs: playback.positionMs,
                                 durationMs: manifest.durationMs)
    }

    /// The chapter being spoken, or the first one before playback starts —
    /// never a hardcoded "Briefing".
    private var title: String {
        playback.currentSegment?.label
            ?? manifest.segment(containing: playback.positionMs)?.label
            ?? manifest.segments.first?.label
            ?? "Today's briefing"
    }

    /// The bar's height at default text sizes. A minimum, not a fixed
    /// height: the title scales with Dynamic Type, and a fixed 56pt let it
    /// spill out of the capsule at the accessibility sizes.
    private static let height: CGFloat = 56

    var body: some View {
        Button(action: onOpen) {
            VStack(spacing: MercurySpace.hair) {
                HStack(spacing: MercurySpace.base) {
                    Image(systemName: playback.isPlaying ? "waveform" : "play.fill")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(MercuryColor.accent.color)
                        .frame(width: 24)

                    Text(title)
                        .font(MercuryType.cardBody)
                        .foregroundStyle(MercuryColor.ink.color)
                        .lineLimit(1)

                    Spacer(minLength: MercurySpace.snug)

                    Text(PlayerTransport.timecode(ms: playback.positionMs))
                        .font(MercuryType.timecode)
                        .foregroundStyle(MercuryColor.inkMuted.color)
                }
                .frame(minHeight: 44)

                // Under the row rather than overlaid on it, so a larger title
                // pushes the line down instead of being drawn through. The
                // extra inset keeps it on the straight run of the capsule's
                // edge, clear of the curve.
                progressLine
                    .padding(.horizontal, MercurySpace.snug)
            }
            .padding(.horizontal, MercurySpace.gutter)
            .padding(.vertical, MercurySpace.tight)
            .frame(maxWidth: .infinity)
            .frame(minHeight: Self.height)
            // A floating capsule over the page, where it used to be an opaque
            // strip ruled off from it. The page scrolls on underneath, which
            // is what glass is for: you can see there is more below without
            // the bar giving up its place. Interactive, because the whole
            // capsule is the button.
            .mercuryGlass(in: Capsule(), interactive: true)
            .contentShape(Capsule())
        }
        // Not `.plain`: it dims the label while pressed, and the glass is in
        // the label, so every tap would drop it below full opacity, which is
        // exactly when it should be showing its own press response.
        .buttonStyle(.mercuryGlass)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(PlayerTransport.spoken(ms: playback.positionMs)) played")
        .accessibilityHint("Opens the player")
        .accessibilityAddTraits(.isButton)
        // Outside the button, so a tap in the margin is a tap on the page.
        // The page gutter, so the capsule lines up with the cards above it.
        .mercuryGutter()
        .padding(.bottom, MercurySpace.tight)
        // A slide with no fade, if the insertion is ever animated: nothing
        // at the call site wraps it in an animation today, so the bar
        // appears without one. Glass under partial opacity stops refracting,
        // so a fading bar would spend its entrance as a flat grey shape.
        .transition(.move(edge: .bottom))
        .animation(motion.resolve(MercuryMotion.present), value: manifest.briefingId)
    }

    /// A hairline, not a bar. The played portion is the day's accent; the
    /// rest is the page's rule colour.
    private var progressLine: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Rectangle().fill(MercuryColor.hairline.color)
                Rectangle()
                    .fill(MercuryColor.accent.color)
                    .frame(width: proxy.size.width * progress)
            }
        }
        .frame(height: 1)
        .accessibilityHidden(true)
    }
}
