import SwiftUI

/// Player Zone 3 (bottom): speed control, pause, and the Join mic (PRD 3.5).
struct TransportControls: View {
    let isPlaying: Bool
    let rate: Float
    let joinState: JoinState
    let onTogglePlay: () -> Void
    let onCycleRate: () -> Void
    let onTapMic: () -> Void

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

                Button(action: onTapMic) {
                    HStack(spacing: 8) {
                        Image(systemName: micIconName)
                            .symbolEffect(.pulse, isActive: joinState == .listening)
                        Text(micLabel)
                    }
                    .font(MercuryType.cardTitle)
                    .foregroundStyle(micIsInteractive ? MercuryColor.textPrimary : MercuryColor.textTertiary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(
                        Capsule().fill(
                            joinState == .listening
                                ? Color.white.opacity(0.18)
                                : Color.white.opacity(0.06)
                        )
                    )
                }
                .disabled(!micIsInteractive)
                .accessibilityLabel(micAccessibilityLabel)

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

    /// Tapping is only meaningful from `.idle` (start) and `.listening`
    /// (cancel) — `JoinStateMachine` would ignore a tap in any other state
    /// anyway, but disabling the button there too means it doesn't even
    /// look tappable mid-answer.
    private var micIsInteractive: Bool {
        joinState == .idle || joinState == .listening
    }

    private var micIconName: String {
        switch joinState {
        case .idle: return "mic.fill"
        case .listening: return "waveform"
        case .thinking: return "ellipsis"
        case .answering: return "waveform.and.mic"
        case .resuming: return "arrow.uturn.left"
        }
    }

    private var micLabel: String {
        switch joinState {
        case .idle: return "Join"
        case .listening: return "Listening…"
        case .thinking: return "Thinking…"
        case .answering: return "Answering…"
        case .resuming: return "Resuming…"
        }
    }

    private var micAccessibilityLabel: String {
        switch joinState {
        case .idle: return "Join — ask the hosts a question"
        case .listening: return "Listening — tap to cancel"
        case .thinking: return "Thinking about your question"
        case .answering: return "The hosts are answering"
        case .resuming: return "Resuming your briefing"
        }
    }
}

#Preview {
    TransportControls(
        isPlaying: true, rate: 1.25, joinState: .idle,
        onTogglePlay: {}, onCycleRate: {}, onTapMic: {}
    )
    .background(Color.black)
}
