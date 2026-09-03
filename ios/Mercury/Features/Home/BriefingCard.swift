import SwiftUI

/// The greeting card. Tapping it plays the briefing — the primary action
/// of the entire app (PRD 3.3).
struct BriefingCard: View {
    let isPlaying: Bool
    let onTap: () -> Void

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            AmbientBackground(seed: "greeting", family: .greeting)
                .blur(radius: 18)

            VStack(alignment: .leading, spacing: 8) {
                if isPlaying {
                    WaveformPill()
                        .padding(.bottom, 4)
                }
                Text("Good morning")
                    .font(MercuryType.greeting)
                    .foregroundStyle(MercuryColor.textPrimary)
                HStack(spacing: 6) {
                    Image(systemName: "calendar")
                        .font(.caption)
                    Text(Date.now.formatted(date: .abbreviated, time: .omitted))
                        .font(MercuryType.caption)
                }
                .foregroundStyle(MercuryColor.textSecondary)
            }
            .padding(20)
        }
        .clipShape(RoundedRectangle(cornerRadius: MercuryColor.cardCornerRadius, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: MercuryColor.cardCornerRadius, style: .continuous))
        .onTapGesture(perform: onTap)
        .accessibilityLabel("Play today's briefing")
    }
}

/// Small animating waveform pill that signals audio is ready. Animates
/// only while playing, and only if the user has not asked for less motion.
struct WaveformPill: View {
    /// The bars loop forever with no user intent behind them, which is the
    /// exact case Reduce Motion exists to switch off. This was the app's only
    /// `repeatForever`, and it ran unconditionally: `MercuryMotionContext` had
    /// no reader anywhere, so the setting reached nothing.
    @Environment(\.mercuryMotion) private var motion
    @State private var animate = false

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<4, id: \.self) { i in
                Capsule()
                    .fill(MercuryColor.textPrimary)
                    .frame(width: 3, height: animate ? CGFloat.random(in: 6...16) : 6)
                    .animation(
                        motion.resolve(MercuryMotion.ambient)?.delay(Double(i) * 0.1),
                        value: animate
                    )
            }
        }
        .frame(height: 16)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.ultraThinMaterial, in: Capsule())
        // Left flat rather than frozen mid-bounce when motion is reduced: the
        // pill still says "playing" by being on screen at all.
        .onAppear { animate = motion.allowsAmbientMotion }
    }
}

#Preview {
    BriefingCard(isPlaying: true, onTap: {})
        .frame(width: 260, height: 360)
        .background(Color.black)
}
