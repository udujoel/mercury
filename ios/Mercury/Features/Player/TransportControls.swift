import SwiftUI

/// Player Zone 3 (bottom): speed control, pause, and a placeholder for the
/// Phase 3 mic. The mic control is rendered disabled — voice interrupt is
/// out of scope for Phase 1.
struct TransportControls: View {
    let isPlaying: Bool
    let rate: Float
    let onTogglePlay: () -> Void
    let onCycleRate: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            HStack(spacing: 24) {
                Button(action: onCycleRate) {
                    Text(rateLabel)
                        .font(MercuryType.chapterTab)
                        .foregroundStyle(MercuryColor.textPrimary)
                        .frame(width: 52, height: 52)
                        .background(Circle().fill(Color.white.opacity(0.12)))
                }

                Button(action: {}) {
                    HStack(spacing: 8) {
                        Image(systemName: "mic.fill")
                        Text("Join")
                    }
                    .font(MercuryType.cardTitle)
                    .foregroundStyle(MercuryColor.textTertiary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(Capsule().fill(Color.white.opacity(0.06)))
                }
                .disabled(true)
                .accessibilityLabel("Join — coming in a later phase")

                Button(action: onTogglePlay) {
                    Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                        .foregroundStyle(.black)
                        .frame(width: 52, height: 52)
                        .background(Circle().fill(MercuryColor.textPrimary))
                }
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
    TransportControls(isPlaying: true, rate: 1.25, onTogglePlay: {}, onCycleRate: {})
        .background(Color.black)
}
