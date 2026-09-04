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

    var body: some View {
        Button(action: onOpen) {
            VStack(spacing: 0) {
                Rectangle()
                    .fill(MercuryColor.hairline.color)
                    .frame(height: 1)

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
                .padding(.horizontal, MercurySpace.gutter)
                .frame(height: 55)   // + the 1pt rule = the 56pt bar

                progressLine
            }
            .frame(maxWidth: .infinity)
            .background(MercuryColor.surfaceRaised.color)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(PlayerTransport.spoken(ms: playback.positionMs)) played")
        .accessibilityHint("Opens the player")
        .accessibilityAddTraits(.isButton)
        .transition(.move(edge: .bottom).combined(with: .opacity))
        .animation(motion.resolve(MercuryMotion.present), value: manifest.briefingId)
    }

    /// A hairline, not a bar. The played portion is the day's accent; the
    /// rest is the same rule that separates the rows above it.
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
