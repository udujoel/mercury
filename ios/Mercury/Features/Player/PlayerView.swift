import SwiftUI

/// The player. The transcript is the page; everything else is a margin note.
///
/// What it was missing, all of which the app already had the data for:
/// elapsed time, duration, a scrubber, and skip. `positionMs` came off the
/// coordinator twenty times a second and was rendered nowhere;
/// `manifest.durationMs` was decoded and never read by any view. It also
/// carried a `Button(action: {})` labelled Share, which did nothing at all
/// when tapped — see the note on `privacyLine` for why that is deleted rather
/// than turned into a `ShareLink`.
struct PlayerView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        let playback = appState.playback
        let manifest = appState.manifest

        VStack(spacing: 0) {
            Rectangle()
                .fill(MercuryColor.accent.color)
                .frame(height: 2)

            TranscriptView(
                turns: manifest?.turns ?? [],
                activeTurn: playback.currentTurn,
                isEnabled: appState.isPlaybackLoaded,
                onSelect: { turn in
                    Task { await appState.play(fromMs: turn.startMs) }
                }
            )
            .frame(maxHeight: .infinity)

            VStack(spacing: MercurySpace.comfortable) {
                // Where you are, centred, directly under the script — the
                // place the eye leaves the text.
                SegmentIndicator(
                    label: playback.currentSegment?.label,
                    position: TranscriptScript.position(
                        ofSegment: playback.currentSegment?.id,
                        in: manifest?.segments ?? []
                    )
                )

                Rectangle()
                    .fill(MercuryColor.hairline.color)
                    .frame(height: 1)

                ScrubBar(
                    positionMs: playback.positionMs,
                    durationMs: manifest?.durationMs ?? 0,
                    isEnabled: appState.isPlaybackLoaded,
                    onSeek: { appState.seek(toMs: $0) }
                )
                .mercuryGutter()

                TransportControls(
                    isPlaying: playback.isPlaying,
                    rate: playback.rate,
                    isEnabled: appState.isPlaybackLoaded,
                    onTogglePlay: togglePlay,
                    onSkip: { delta in
                        appState.seek(toMs: PlayerTransport.skipTarget(
                            positionMs: playback.positionMs,
                            byMs: delta,
                            durationMs: manifest?.durationMs ?? 0
                        ))
                    },
                    onCycleRate: { playback.cycleRate() }
                )
                .mercuryGutter()

                ChapterTabs(
                    segments: manifest?.segments ?? [],
                    activeSegmentID: playback.currentSegment?.id,
                    isEnabled: appState.isPlaybackLoaded,
                    // Plays rather than merely seeking: a chapter tab is a
                    // request to hear that chapter.
                    onSelect: { segment in
                        Task { await appState.play(fromMs: segment.startMs) }
                    }
                )

                privacyLine
            }
            .padding(.bottom, MercurySpace.snug)
        }
        .background(MercuryColor.surface.color.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) { masthead }
            if let manifest, !manifest.sources.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        ShowNotesView(manifest: manifest,
                                      onSeek: { ms in
                                          Task { await appState.play(fromMs: ms) }
                                      })
                    } label: {
                        Image(systemName: "list.bullet.rectangle")
                            .foregroundStyle(MercuryColor.inkMuted.color)
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .accessibilityLabel("Show notes")
                    .accessibilityHint("Lists every source this briefing used")
                    // Every entry deep-links into the audio, so it carries
                    // the same hazard as the chapter ribbon: with nothing
                    // loaded, an offset from a restored briefing would be
                    // applied to today's.
                    .disabled(!appState.isPlaybackLoaded)
                }
            }
        }
    }

    /// Play, pause, or *fetch first* — because there is a state where the
    /// player is on screen with a manifest and no audio.
    ///
    /// `AppState.restoreLastBriefing()` runs at launch and sets `manifest`
    /// from SwiftData without touching the coordinator, so the mini-player
    /// appears (correctly — there is something to return to) and taps through
    /// to a player whose transcript renders and whose play button did
    /// nothing: `PlaybackCoordinator.play()` opens with `guard file != nil
    /// else { return }`. That is exactly the dead control this task deletes
    /// from the transport row, arriving through a different door.
    ///
    /// `loadState == .ready` is the available signal for "a briefing is in
    /// the engine" — every path that reaches `playback.load(…)` sets it, and
    /// nothing else does. It is a proxy, and the honest fix is a `isLoaded`
    /// on the coordinator, which lives in `Core/` and is not this task's to
    /// add. Flagged in the report.
    /// The same one place Home's card goes through — see
    /// `AppState.togglePlayback`.
    private func togglePlay() {
        Task { await appState.togglePlayback() }
    }

    /// "BRIEFING · 4 SEP".
    ///
    /// From `generatedAt`, not from `briefingId`. The id is a date string the
    /// backend derives in the SERVER's timezone (see `BriefingClock`), so
    /// printing it raw shows a reader in another zone a date that is not
    /// theirs and is not formatted for their locale either.
    @ViewBuilder
    private var masthead: some View {
        if let generated = appState.manifest?.generatedAt {
            Text("Briefing · \(generated.formatted(.dateTime.day().month(.abbreviated)))")
                .font(MercuryType.eyebrow)
                .textCase(.uppercase)
                .tracking(0.6)
                .foregroundStyle(MercuryColor.inkFaint.color)
        } else {
            Text("Briefing")
                .font(MercuryType.eyebrow)
                .textCase(.uppercase)
                .tracking(0.6)
                .foregroundStyle(MercuryColor.inkFaint.color)
        }
    }

    /// The Share button that sat here is gone rather than promoted to a real
    /// `ShareLink`, and that is a deliberate disagreement with the brief.
    ///
    /// The only thing there is to share is `manifest.audioUrl` — six minutes
    /// of two hosts reading this user's calendar and inbox aloud, at a URL
    /// that is a plain object in R2. A one-tap control that puts that link in
    /// a group chat sits directly under a label promising the opposite, and
    /// PRD rule 5 exists because the contents are exactly this sensitive.
    ///
    /// "Delete it or make it real" is right, and deleting it is the honest
    /// half. Sharing a briefing is a feature that needs an expiring link
    /// before it needs a button.
    private var privacyLine: some View {
        Label("Private to this device", systemImage: "lock.fill")
            .font(MercuryType.caption)
            .foregroundStyle(MercuryColor.inkFaint.color)
            .frame(maxWidth: .infinity)
            .accessibilityLabel("This briefing is private to this device")
    }
}

#Preview {
    NavigationStack {
        PlayerView()
            .environment(AppState())
    }
}
