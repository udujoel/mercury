import SwiftUI

/// The transport row: back 15, play/pause, forward 15, speed.
///
/// **Why the invisible rate button on the left.** The brief asked for four
/// controls in an `HStack(spacing: 0)` at `maxWidth: .infinity` so play lands
/// on the axis by construction. Four equal cells put play's centre at 3/8 of
/// the width, not 1/2 — closer than the 38pt error it replaces, but still off,
/// and off in a way that no amount of equal-width machinery fixes.
///
/// So the row is five cells: a copy of the speed button, drawn at zero opacity
/// and hidden from VoiceOver, then the three flexible controls, then the real
/// speed button. The two fixed cells are the same view with the same label, so
/// they have identical intrinsic width at every Dynamic Type size and in every
/// locale — the balance cannot drift, which is what "by construction" has to
/// mean. Play is cell three of three flexible cells between two equal fixed
/// ones, so its centre is the screen's centre exactly.
///
/// The alternative — an overlay pinned trailing — does not participate in
/// layout, so at accessibility sizes the speed control simply draws on top of
/// the forward-skip button.
struct TransportControls: View {
    @Environment(\.dynamicTypeSize) private var typeSize

    let isPlaying: Bool
    let rate: Float
    /// False until there is audio in the engine. Skip and speed act on a
    /// coordinator that would ignore them, so they are shown disabled rather
    /// than silently doing nothing. Play stays enabled at all times — it is
    /// the control that loads.
    var isEnabled: Bool = true
    let onTogglePlay: () -> Void
    let onSkip: (Int) -> Void
    let onCycleRate: () -> Void

    var body: some View {
        Group {
            if typeSize.isAccessibilitySize {
                // The balancing twin costs a full rate-button width, and at
                // accessibility sizes that is the width of a grown "1.25x".
                // Five cells stop fitting: on a compact device the row
                // overflows its content width and the speed label truncates.
                //
                // The twin exists only to centre play, so at these sizes the
                // speed control moves to its own line and play is centred by
                // construction instead — three equal flexible cells — which
                // also gives the label the width it now wants.
                VStack(spacing: MercurySpace.snug) {
                    HStack(spacing: 0) { transport }
                    rateButton
                }
            } else {
                HStack(spacing: 0) {
                    rateButton
                        .opacity(0)
                        .accessibilityHidden(true)
                        .allowsHitTesting(false)

                    transport

                    rateButton
                }
            }
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var transport: some View {
        skip(by: -PlayerTransport.skipInterval,
             symbol: "gobackward.15",
             label: "Back 15 seconds")

        playButton

        skip(by: PlayerTransport.skipInterval,
             symbol: "goforward.15",
             label: "Forward 15 seconds")
    }

    private var playButton: some View {
        Button(action: onTogglePlay) {
            Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                .font(.system(size: 26, weight: .medium))
                .foregroundStyle(MercuryColor.inkOnAccent.color)
                // A FILL with a glyph on it, not ink — see the note in
                // ChapterTabs. `inkOnAccent` is the token that exists for
                // exactly this.
                .frame(width: 64, height: 64)
                .background(Circle().fill(MercuryColor.accent.color))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .accessibilityLabel(isPlaying ? "Pause" : "Play")
    }

    private func skip(by delta: Int, symbol: String, label: String) -> some View {
        Button { onSkip(delta) } label: {
            Image(systemName: symbol)
                .font(.system(size: 22, weight: .regular))
                .foregroundStyle(MercuryColor.ink.color)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .disabled(!isEnabled)
        .accessibilityLabel(label)
    }

    private var rateButton: some View {
        Button(action: onCycleRate) {
            Text(PlayerTransport.rateLabel(rate))
                .font(MercuryType.chapterTab)
                .monospacedDigit()
                .foregroundStyle(MercuryColor.ink.color)
                .lineLimit(1)
                .padding(.horizontal, MercurySpace.snug)
                // 44pt in both directions. The old rate control was a 52pt
                // circle and the two label buttons beside it were text with
                // no frame at all — three of the app's controls under the
                // minimum on one screen.
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
        .accessibilityLabel("Playback speed")
        .accessibilityValue(PlayerTransport.rateLabel(rate))
        .accessibilityHint("Cycles through 1x, 1.25x, 1.5x and 2x")
    }
}

#Preview {
    VStack {
        TransportControls(isPlaying: true, rate: 1.25,
                          onTogglePlay: {}, onSkip: { _ in }, onCycleRate: {})
        TransportControls(isPlaying: false, rate: 1.0,
                          onTogglePlay: {}, onSkip: { _ in }, onCycleRate: {})
    }
    .background(MercuryColor.surface.color)
}
