import SwiftUI

/// Player Zone 1 (top 60%): full-bleed ambient background derived from the
/// current segment, with the currently spoken sentence as large centred
/// text that cross-fades on change — 220ms ease, never a hard cut.
struct TranscriptView: View {
    let segment: BriefingSegment?
    let turn: Turn?
    let isPlaying: Bool

    var body: some View {
        ZStack(alignment: .topTrailing) {
            AmbientBackground(seed: segment?.id ?? "briefing", family: .derived)

            if isPlaying {
                WaveformPill()
                    .padding(16)
            }

            VStack {
                Spacer()
                Text(turn?.text ?? " ")
                    .font(MercuryType.transcript)
                    .multilineTextAlignment(.center)
                    .lineSpacing(6)
                    .foregroundStyle(MercuryColor.textPrimary)
                    .padding(.horizontal, 28)
                    .id(turn?.id)
                    .transition(.opacity.animation(.easeInOut(duration: 0.22)))
                Spacer()
            }
        }
    }
}

#Preview {
    TranscriptView(
        segment: BriefingSegment(id: "inbox", label: "Your inbox", startMs: 0),
        turn: Turn(speaker: "A", segment: "inbox",
                   text: "Priya sent over the Q3 planning doc — she wants eyes on it before Thursday.",
                   startMs: 0, endMs: 4000),
        isPlaying: true
    )
}
