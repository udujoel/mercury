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
    /// height: the title scales with Dynamic Type, and a fixed 56pt would
    /// let it spill out of the capsule at the accessibility sizes.
    private static let height: CGFloat = 56

    var body: some View {
        Button(action: onOpen) {
            VStack(spacing: MercurySpace.hair) {
                HStack(spacing: MercurySpace.base) {
                    Image(systemName: playback.isPlaying ? "waveform" : "play.fill")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(MercuryColor.accent.color)
                        .frame(width: 24)

                    // Hierarchical styles, not the ink tokens. The tokens
                    // follow the system appearance only, and glass changes its
                    // own lightness with what scrolls under it: over a dark
                    // photo card in light mode it darkens while `ink` stays
                    // near-black. `.primary` and `.secondary` get the
                    // vibrancy pass on glass and on the material fallback.
                    Text(title)
                        .font(MercuryType.cardBody)
                        .foregroundStyle(.primary)
                        .lineLimit(1)

                    Spacer(minLength: MercurySpace.snug)

                    Text(PlayerTransport.timecode(ms: playback.positionMs))
                        .font(MercuryType.timecode)
                        .foregroundStyle(.secondary)
                }
                .frame(minHeight: 44)

                // Under the row rather than overlaid on it, so a larger title
                // pushes the line down instead of being drawn through. At the
                // default height the extra inset equals the capsule's radius,
                // which keeps the line on the straight run of the edge.
                progressLine
                    .padding(.horizontal, MercurySpace.snug)
            }
            .padding(.horizontal, MercurySpace.gutter)
            .padding(.vertical, MercurySpace.tight)
            .frame(maxWidth: .infinity)
            .frame(minHeight: Self.height)
        }
        // A floating glass capsule over the page, where it used to be an
        // opaque strip ruled off from it. The page scrolls on underneath,
        // which is what glass is for: you can see there is more below without
        // the bar giving up its place. The style draws the glass, so the
        // whole capsule is the button.
        .buttonStyle(.mercuryGlassCapsule)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(PlayerTransport.spoken(ms: playback.positionMs)) played")
        .accessibilityHint("Opens the player")
        .accessibilityAddTraits(.isButton)
        // Outside the button, so a tap in the margin is a tap on the page.
        // The page gutter, so the capsule lines up with the cards above it.
        .mercuryGutter()
        // `snug` rather than `tight`: on a Home-button iPhone there is no
        // bottom safe area, and 4pt left the capsule nearly on the bezel.
        .padding(.bottom, MercurySpace.snug)
        // A slide with no fade, if the insertion is ever animated: nothing
        // at the call site wraps it in an animation today, so the bar
        // appears without one. Glass under partial opacity stops refracting,
        // so a fading bar would spend its entrance as a flat grey shape.
        .transition(.move(edge: .bottom))
        .animation(motion.resolve(MercuryMotion.present), value: manifest.briefingId)
    }

    /// A hairline, not a bar. The played portion is the day's accent. The
    /// track is a hierarchical fill rather than the `hairline` token, which is
    /// tuned to separate rows on opaque paper and all but vanishes on glass.
    private var progressLine: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Rectangle().fill(.quaternary)
                Rectangle()
                    .fill(MercuryColor.accent.color)
                    .frame(width: proxy.size.width * progress)
            }
        }
        .frame(height: 1)
        .accessibilityHidden(true)
    }
}
