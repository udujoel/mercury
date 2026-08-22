import SwiftUI

/// Visible feedback for every `JoinState` (phase brief: "give every state
/// visible feedback"). Sits above `TransportControls` and only takes up
/// space while a Join interaction is in progress — `.idle` renders nothing.
struct JoinOverlay: View {
    let state: JoinState
    let partialTranscript: String
    let lastError: String?

    var body: some View {
        Group {
            switch state {
            case .idle:
                EmptyView()

            case .listening:
                statusRow(
                    icon: "waveform",
                    label: partialTranscript.isEmpty ? "Listening…" : partialTranscript,
                    animated: true
                )

            case .thinking:
                statusRow(icon: "ellipsis", label: "Thinking…", animated: true)

            case .answering:
                statusRow(icon: "waveform.and.mic", label: "Answering…", animated: true)

            case .resuming:
                statusRow(icon: "arrow.uturn.left", label: "Back to your briefing…", animated: false)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: state)
    }

    private func statusRow(icon: String, label: String, animated: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .foregroundStyle(MercuryColor.textPrimary)
                .symbolEffect(.pulse, isActive: animated)
            Text(label)
                .font(MercuryType.caption)
                .foregroundStyle(MercuryColor.textSecondary)
                .lineLimit(1)
            Spacer()
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 8)
        .transition(.opacity.combined(with: .move(edge: .bottom)))
    }
}

#Preview("Listening") {
    JoinOverlay(state: .listening, partialTranscript: "what's the weather like", lastError: nil)
        .background(Color.black)
}

#Preview("Thinking") {
    JoinOverlay(state: .thinking, partialTranscript: "", lastError: nil)
        .background(Color.black)
}

#Preview("Answering") {
    JoinOverlay(state: .answering, partialTranscript: "", lastError: nil)
        .background(Color.black)
}

#Preview("Resuming") {
    JoinOverlay(state: .resuming, partialTranscript: "", lastError: nil)
        .background(Color.black)
}
