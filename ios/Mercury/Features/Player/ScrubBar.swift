import SwiftUI

/// The progress line and the two timecodes.
///
/// The player had none of this: no elapsed, no duration, no way to move. Every
/// number here comes from data the app already held — `positionMs` off the
/// coordinator, `durationMs` off the manifest — so this is a control that was
/// missing rather than a capability that was.
struct ScrubBar: View {
    let positionMs: Int
    let durationMs: Int
    /// False until there is audio in the engine. Defaulted so the preview and
    /// every existing call site keep working unchanged.
    var isEnabled: Bool = true
    let onSeek: (Int) -> Void

    /// Non-nil while a finger is down. The coordinator publishes a new
    /// position twenty times a second, so without a local override the line
    /// snaps back under the finger between drag events and the handle
    /// stutters. Committed on release.
    @State private var draggingMs: Int?

    private var shownMs: Int { draggingMs ?? positionMs }

    private var progress: Double {
        PlayerTransport.progress(positionMs: shownMs, durationMs: durationMs)
    }

    var body: some View {
        VStack(spacing: MercurySpace.snug) {
            track
            timecodes
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Playback position")
        .accessibilityValue(
            PlayerTransport.progressAccessibilityValue(positionMs: positionMs,
                                                       durationMs: durationMs)
        )
        // Swipe up/down on the scrubber moves by the same 15 seconds the skip
        // buttons do, so a VoiceOver user has a way to move through the
        // briefing that does not require hitting a 2pt line.
        .accessibilityAdjustableAction { direction in
            let delta = direction == .increment
                ? PlayerTransport.skipInterval
                : -PlayerTransport.skipInterval
            onSeek(PlayerTransport.skipTarget(positionMs: positionMs,
                                              byMs: delta,
                                              durationMs: durationMs))
        }
    }

    private var track: some View {
        GeometryReader { proxy in
            let width = proxy.size.width
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(MercuryColor.hairline.color)
                    .frame(height: 2)
                Capsule()
                    .fill(MercuryColor.accent.color)
                    .frame(width: max(width * progress, 0), height: 2)
                Circle()
                    .fill(MercuryColor.accent.color)
                    .frame(width: 10, height: 10)
                    .offset(x: max(width * progress - 5, -5))
            }
            .frame(height: proxy.size.height, alignment: .center)
            .contentShape(Rectangle())
            .gesture(
                // `minimumDistance: 0` so a tap anywhere on the line seeks
                // there, which is what a 2pt target needs to be usable at all.
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        draggingMs = PlayerTransport.seekTarget(
                            atX: value.location.x, trackWidth: width, durationMs: durationMs)
                    }
                    .onEnded { value in
                        let target = PlayerTransport.seekTarget(
                            atX: value.location.x, trackWidth: width, durationMs: durationMs)
                        draggingMs = nil
                        onSeek(target)
                    }
            )
            // A drag on a coordinator with no file schedules nothing and
            // reports nothing, so on the restore path — a manifest decoded
            // from disk, no audio loaded — the scrubber moved under the
            // finger and sprang back. Disabled says that; a live-looking
            // control that ignores you does not. It is not wired to fetch:
            // dragging a scrubber is not a request to download a briefing.
            .disabled(!isEnabled)
            .opacity(isEnabled ? 1 : 0.4)
        }
        // The line is 2pt; the gesture area is 44. A hairline you cannot hit
        // is decoration.
        .frame(height: 44)
    }

    private var timecodes: some View {
        HStack {
            Text(PlayerTransport.timecode(ms: shownMs))
            Spacer()
            Text(PlayerTransport.remainingLabel(positionMs: shownMs, durationMs: durationMs))
        }
        // Tabular figures: without them the digits shift sideways as the
        // seconds tick, next to a line that is itself moving.
        .font(MercuryType.timecode)
        .foregroundStyle(MercuryColor.inkMuted.color)
    }
}

#Preview {
    ScrubBar(positionMs: 134_000, durationMs: 360_000, onSeek: { _ in })
        .padding()
        .background(MercuryColor.surface.color)
}
