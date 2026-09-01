import SwiftUI

/// Player Zone 3 (bottom): speed control and pause.
///
/// The Join mic that used to sit between them is gone — see ADR-009.
/// PRD 3.4 made it the largest touch target on the screen; with the
/// feature dropped, the two remaining controls get the width instead of
/// leaving a hole where it was.
struct TransportControls: View {
    let isPlaying: Bool
    let rate: Float
    let onTogglePlay: () -> Void
    let onCycleRate: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            HStack(spacing: 24) {
                Spacer()
                Button(action: onCycleRate) {
                    Text(rateLabel)
                        .font(MercuryType.chapterTab)
                        .foregroundStyle(MercuryColor.ink.color)
                        .frame(width: 52, height: 52)
                        .background(Circle().fill(MercuryColor.hairline.color))
                }

                Button(action: onTogglePlay) {
                    Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                        // Fill, not text — see the note in ChapterTabs.
                        .foregroundStyle(MercuryColor.inkOnAccent.color)
                        .frame(width: 72, height: 72)
                        .background(Circle().fill(MercuryColor.accent.color))
                }
                Spacer()
            }
            .padding(.horizontal, 20)

            HStack {
                Label("Private", systemImage: "lock.fill")
                    .font(MercuryType.caption)
                    .foregroundStyle(MercuryColor.textTertiary)
                Spacer()
                Button(action: {}) {
                    Label("Share", systemImage: "square.and.arrow.up")
                        .font(MercuryType.caption)
                        .foregroundStyle(MercuryColor.textTertiary)
                }
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 8)
        }
    }

    private var rateLabel: String {
        rate == rate.rounded() ? "\(Int(rate))x" : String(format: "%.2gx", Double(rate))
    }
}

#Preview {
    TransportControls(
        isPlaying: true, rate: 1.25,
        onTogglePlay: {}, onCycleRate: {}
    )
    .background(Color.black)
}
