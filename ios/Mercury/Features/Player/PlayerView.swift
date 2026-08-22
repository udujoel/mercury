import SwiftUI

struct PlayerView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        let playback = appState.playback

        VStack(spacing: 0) {
            TranscriptView(
                segment: playback.currentSegment,
                turn: playback.currentTurn,
                isPlaying: playback.isPlaying
            )
            .frame(maxHeight: .infinity)

            VStack(spacing: 12) {
                ChapterTabs(
                    segments: appState.manifest?.segments ?? [],
                    activeSegmentID: playback.currentSegment?.id,
                    onSelect: { segment in playback.seek(toMs: segment.startMs) }
                )

                JoinOverlay(
                    state: appState.join.state,
                    partialTranscript: appState.join.partialTranscript,
                    lastError: appState.join.lastError
                )

                if let manifest = appState.manifest {
                    HStack {
                        Text("Briefing · \(manifest.briefingId)")
                            .font(MercuryType.caption)
                            .foregroundStyle(MercuryColor.textSecondary)
                        Spacer()
                        if !manifest.sources.isEmpty {
                            NavigationLink {
                                ShowNotesView(manifest: manifest, onSeek: { ms in
                                    playback.seek(toMs: ms)
                                })
                            } label: {
                                Image(systemName: "list.bullet.rectangle")
                                    .foregroundStyle(MercuryColor.textSecondary)
                            }
                        }
                    }
                    .padding(.horizontal, 20)
                }

                TransportControls(
                    isPlaying: playback.isPlaying,
                    rate: playback.rate,
                    joinState: appState.join.state,
                    onTogglePlay: {
                        playback.isPlaying ? playback.pause() : playback.play()
                    },
                    onCycleRate: { playback.cycleRate() },
                    onTapMic: { appState.join.tap() }
                )
            }
            .padding(.top, 8)
            .background(MercuryColor.canvas)
        }
        .background(MercuryColor.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
    }
}

#Preview {
    NavigationStack {
        PlayerView()
            .environment(AppState())
    }
}
