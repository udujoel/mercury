import SwiftUI

/// Home, in the editorial register (ADR-012).
///
/// What this replaces: a 3D fanned deck of four cards, three of which were
/// filled with invented content. The deck was a dark-ambient device — it read
/// as depth because luminous cards floated on black — and on warm off-white
/// paper four white cards overlapping at 18pt offsets read as a misaligned
/// stack, with the content you came for hidden behind a drag gesture you had
/// to discover. `CardDeck.swift` is deleted rather than retuned; see ADR-012
/// for why that is a deletion and not a sizing fix.
///
/// The replacement is a page: a masthead, a greeting, one primary action, and
/// a list. The day's accent appears exactly twice — the masthead rule and the
/// play glyph — and everything else is ink on paper.
struct HomeView: View {
    @Environment(AppState.self) private var appState

    @State private var showPlayer = false
    @State private var selectedStory: DigestStory?
    @State private var showArchive = false
    @State private var showSettings = false
    @State private var regenerateNote: String?

    /// Says what actually happens next, which is nothing visible for some
    /// minutes. A spinner that finishes while the briefing is still being
    /// made would read as "done" and send the user to a player holding the
    /// old audio.
    private static let startedNote =
        "Rebuilding from your latest mail. It takes a few minutes; the new "
        + "briefing appears the next time you press play."

    private var rows: [BriefingDigest.Row] { BriefingDigest.rows(for: appState.manifest) }

    /// The day the briefing on screen was made, which is not always today.
    ///
    /// `restoreLastBriefing` puts the last briefing on the device into
    /// `manifest`, so before the cron has run — or with the phone offline —
    /// Home renders yesterday's rows and yesterday's runtime underneath a
    /// masthead showing today's date, a card headed "Today's briefing" and a
    /// section labelled "In today's briefing". Four true-looking statements
    /// about the wrong day.
    ///
    /// "Today's briefing", or the actual day when what is on the device is
    /// older — see `BriefingDigest.title`, which owns the comparison so the
    /// overnight-generation case can be tested rather than reasoned about.
    private var briefingTitle: String {
        BriefingDigest.title(briefingId: appState.manifest?.briefingId,
                             generatedAt: appState.manifest?.generatedAt)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Masthead(date: .now)

                    Text("Good morning")
                        .font(MercuryType.greeting)
                        .foregroundStyle(MercuryColor.ink.color)
                        .padding(.top, MercurySpace.generous)
                        .padding(.bottom, MercurySpace.generous)
                        // The date is already in the masthead directly above;
                        // VoiceOver reading it twice in a row is noise.
                        .accessibilityAddTraits(.isHeader)

                    TodaysBriefingCard(
                        title: briefingTitle,
                        state: appState.loadState,
                        summary: BriefingDigest.summary(for: appState.manifest),
                        isPlaying: appState.playback.isPlaying,
                        onPlay: play,
                        // NOT `play`. Retry follows a failed fetch, and
                        // `togglePlayback` would pause or resume whatever
                        // audio happens to be loaded instead of trying the
                        // fetch again — which is the one thing the button
                        // says it does.
                        onRetry: { Task { await appState.playBriefing() } }
                    )

                    // The desk: the stories themselves. Above the rows
                    // below, which summarise the same morning by segment.
                    // `deskStories` rather than the digest directly: the
                    // cards must belong to the briefing on screen, or their
                    // turn offsets address a manifest that is not loaded.
                    if !deskStories.isEmpty {
                        SectionLabel("Today's desk")
                            .padding(.top, MercurySpace.section)
                            .padding(.bottom, MercurySpace.snug)

                        DeskFeed(stories: deskStories) { selectedStory = $0 }
                    }

                    if !rows.isEmpty {
                        SectionLabel("In \(briefingTitle.lowercased())")
                            .padding(.top, MercurySpace.section)
                            .padding(.bottom, MercurySpace.snug)

                        // Disabled mid-load: a second tap would call
                        // `play(fromMs:)` while `playBriefing` is still in
                        // flight, and the guard inside it would return
                        // having silently dropped the chapter the user asked
                        // for.
                        BriefingRows(rows: rows, onSelect: open(at:))
                            .disabled(appState.loadState == .loading)
                    }

                    RegenerateButton(
                        isRunning: appState.isRegenerating,
                        remaining: appState.regeneration?.remaining,
                        onConfirm: {
                            Task {
                                do {
                                    _ = try await appState.regenerateToday()
                                    regenerateNote = Self.startedNote
                                } catch {
                                    regenerateNote = error.localizedDescription
                                }
                            }
                        }
                    )
                    .padding(.top, MercurySpace.section)

                    if let regenerateNote {
                        Text(regenerateNote)
                            .font(MercuryType.figureCaption)
                            .foregroundStyle(MercuryColor.inkFaint.color)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, MercurySpace.tight)
                    }

                    Button { showArchive = true } label: {
                        HStack(spacing: MercurySpace.tight) {
                            Text("Earlier briefings")
                                .font(MercuryType.cardBody)
                            Image(systemName: "chevron.right").font(.footnote)
                            Spacer(minLength: 0)
                        }
                        .foregroundStyle(MercuryColor.inkMuted.color)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .padding(.top, MercurySpace.section)
                }
                .mercuryGutter()
                .padding(.top, MercurySpace.snug)
                .padding(.bottom, MercurySpace.section)
            }
            .background(MercuryColor.surface.color.ignoresSafeArea())
            .safeAreaInset(edge: .bottom, spacing: 0) {
                // Only when there is something to return to. This is the fix
                // for the standing complaint that leaving the player
                // restarted the briefing: it did, because the only way back
                // in was the greeting card, which called `playBriefing()` and
                // reloaded the coordinator from zero. The bar navigates
                // instead of loading.
                if let manifest = appState.manifest {
                    MiniPlayerBar(
                        manifest: manifest,
                        playback: appState.playback,
                        onOpen: { showPlayer = true }
                    )
                }
            }
            .navigationDestination(isPresented: $showPlayer) {
                PlayerView()
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape")
                            .foregroundStyle(MercuryColor.inkMuted.color)
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .accessibilityLabel("Settings")
                }
            }
            .navigationDestination(isPresented: $showSettings) {
                SettingsView()
            }
            .navigationDestination(isPresented: $showArchive) {
                ArchiveView(
                    ids: appState.archive,
                    currentId: appState.manifest?.briefingId,
                    failed: appState.archiveFailed,
                    loading: appState.archiveLoading,
                    busy: appState.loadState == .loading,
                    onSelect: { id in
                        showArchive = false
                        Task { await appState.openBriefing(id: id) }
                    },
                    onRefresh: { await appState.loadArchive() }
                )
                .task { await appState.loadArchive() }
            }
            .navigationDestination(item: $selectedStory) { story in
                StoryDetailView(
                    story: story,
                    // Absent rather than disabled when the hosts did not
                    // discuss it: there is no audio to enable it for.
                    onPlay: story.turnIndex == nil ? nil : { index in
                        selectedStory = nil
                        Task { await playStory(turnIndex: index) }
                    }
                )
            }
            // The digest is a SIBLING of the manifest (ADR-013), fetched
            // here rather than on the play path — pressing play must never
            // wait on a screen you may not open.
            .task(id: appState.manifest?.briefingId) { await appState.loadDigest() }
            // Covers playback started outside a direct tap on the card — the
            // wake notification's "Play" action or tapping the notification
            // itself (PRD F-17), which drive `AppState.playPrefetched`
            // directly rather than going through this view's handler.
            .onChange(of: appState.playback.isPlaying) { _, isPlaying in
                if isPlaying { showPlayer = true }
            }
        }
    }

    /// Toggles when there is audio loaded, fetches when there is not.
    ///
    /// It used to call `playBriefing()` either way, so the pause affordance
    /// on a playing card restarted the briefing from zero — a full manifest
    /// fetch and audio download to undo the thing the user asked for.
    ///
    /// Navigation is left to the `isPlaying` observer below: resuming opens
    /// the player, pausing stays on Home, and the notification path gets the
    /// same treatment without a second rule.
    private func play() {
        Task {
            await appState.togglePlayback()
            // Only when something is actually playing. `loadState` stays
            // `.ready` through a pause, so testing it navigated the user off
            // Home for pressing pause — which is the same class of mistake
            // as the restart this method was fixing.
            if appState.playback.isPlaying { showPlayer = true }
        }
    }

    /// Tapping a row goes to the player *at that chapter*, and plays.
    ///
    /// Both halves were missing. The seek went to a coordinator that may hold
    /// no file — on the restore path it always does, and `seek` there
    /// schedules nothing — and on a merely paused briefing it moved the
    /// playhead and left the audio stopped, while the row's own VoiceOver
    /// hint said it plays this part of the briefing.
    /// The desk, ordered — and empty unless the digest belongs to the
    /// briefing currently on screen.
    ///
    /// The id check is not belt-and-braces. `loadDigest` clears before
    /// fetching and rechecks after, but Home renders on every state change
    /// including the ones mid-flight, and a story card whose `turnIndex`
    /// addresses a manifest that is not loaded seeks the wrong audio.
    ///
    /// Covered stories lead, in the order the hosts discussed them: the
    /// audio order is the only ordering the morning actually has, and
    /// alphabetical or by source count would both be arbitrary.
    private var deskStories: [DigestStory] {
        guard let digest = appState.digest,
              digest.briefingId == appState.manifest?.briefingId
        else { return [] }
        // Uncapped on purpose. `DeskFeed` applies the limit AFTER choosing
        // its lead — capping here picked the most-covered story of the first
        // twelve rather than of the morning.
        return digest.covered + digest.uncovered
    }

    /// Opens the player at the turn where this story was discussed.
    ///
    /// `renderedTurns`, matching what `turn_index` indexes: the backend
    /// compacts the turn list before writing the manifest, so the raw array
    /// and the index would disagree by however many turns failed to
    /// synthesise.
    private func playStory(turnIndex: Int) async {
        guard let manifest = appState.manifest,
              manifest.renderedTurns.indices.contains(turnIndex)
        else { return }

        let target = manifest.renderedTurns[turnIndex].startMs

        // With nothing loaded, `play(fromMs:)` fetches TODAY — so on a
        // restored older briefing it would apply that briefing's offset to
        // today's audio, and the story you tapped would not be the one you
        // heard. Load the briefing the offset belongs to first.
        if !appState.isPlaybackCurrent {
            await appState.openBriefing(id: manifest.briefingId)
            guard appState.isPlaybackCurrent else { return }
        }

        showPlayer = true
        await appState.play(fromMs: target)
    }

    private func open(at row: BriefingDigest.Row) {
        guard let ms = row.seekMs else { return }
        Task {
            await appState.play(fromMs: ms)
            // Navigate only once there is something to navigate TO. Setting
            // this first meant an offline tap landed the user on a player
            // with every control disabled, while the error explaining why
            // stayed behind on Home.
            guard appState.isPlaybackLoaded else { return }
            showPlayer = true
        }
    }
}

// MARK: - Masthead

/// The date, then a 2pt rule in the day's accent. One of exactly two places
/// colour appears on this screen.
struct Masthead: View {
    let date: Date

    /// "SATURDAY, 4 SEPTEMBER". Uppercased by `textCase`, not by baking caps
    /// into the string, so VoiceOver still reads it as words rather than
    /// spelling it.
    private var line: String {
        date.formatted(.dateTime.weekday(.wide).day().month(.wide))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: MercurySpace.snug) {
            Text(line)
                .font(MercuryType.eyebrow)
                .textCase(.uppercase)
                .tracking(0.6)
                .foregroundStyle(MercuryColor.inkFaint.color)

            Rectangle()
                .fill(MercuryColor.accent.color)
                .frame(height: 2)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(line)
    }
}

// MARK: - Section label

struct SectionLabel: View {
    private let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(MercuryType.eyebrow)
            .textCase(.uppercase)
            .tracking(0.6)
            .foregroundStyle(MercuryColor.inkFaint.color)
            .accessibilityAddTraits(.isHeader)
    }
}

// MARK: - The rows

/// Editorial rows: hairline separated, no card chrome, no gradients, no
/// shadows. Each row is one line of the briefing's own contents page.
struct BriefingRows: View {
    let rows: [BriefingDigest.Row]
    let onSelect: (BriefingDigest.Row) -> Void

    var body: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(MercuryColor.hairline.color)
                .frame(height: 1)
            ForEach(rows) { row in
                BriefingRowView(row: row) { onSelect(row) }
                Rectangle()
                    .fill(MercuryColor.hairline.color)
                    .frame(height: 1)
            }
        }
    }
}

struct BriefingRowView: View {
    @Environment(\.dynamicTypeSize) private var typeSize

    let row: BriefingDigest.Row
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            content
                // 44pt is the floor, not the height: the row grows with the
                // type. Every control on this screen clears it.
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, MercurySpace.snug)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!row.isInBriefing)
        .accessibilityLabel("\(row.label), \(row.detail)")
        .accessibilityHint(row.isInBriefing ? "Plays this part of the briefing" : "")
        .accessibilityAddTraits(row.isInBriefing ? .isButton : .isStaticText)
    }

    @ViewBuilder
    private var content: some View {
        // At accessibility sizes a label, a detail and a chevron on one line
        // truncate to nothing. Stack them instead of shrinking them.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: MercurySpace.tight) {
                HStack(spacing: MercurySpace.snug) {
                    label
                    Spacer(minLength: 0)
                    chevron
                }
                detail
            }
        } else {
            HStack(spacing: MercurySpace.base) {
                label
                Spacer(minLength: MercurySpace.snug)
                detail
                chevron
            }
        }
    }

    private var label: some View {
        Text(row.label)
            .font(MercuryType.cardBody)
            .foregroundStyle(row.isInBriefing
                             ? MercuryColor.ink.color
                             : MercuryColor.inkMuted.color)
    }

    private var detail: some View {
        Text(row.detail)
            .font(MercuryType.figureCaption)
            .foregroundStyle(MercuryColor.inkMuted.color)
            .multilineTextAlignment(.leading)
    }

    @ViewBuilder
    private var chevron: some View {
        // No chevron on a row that goes nowhere. An affordance pointing at
        // an action that does not exist is the same defect as the player's
        // dead Share button.
        if row.isInBriefing {
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(MercuryColor.inkFaint.color)
        }
    }
}

// MARK: - Error presentation

/// Turns `LoadState.failed`'s string into something a person can read.
///
/// The string is `error.localizedDescription`, and `APIError.errorDescription`
/// builds it for a log rather than for a reader — it interpolates the
/// underlying error with `\(error)`, which is the *debug* description. So a
/// dropped connection reached the screen as
///
///     Network error: Error Domain=NSURLErrorDomain Code=-1004
///     "Could not connect to the server." UserInfo={_kCFStreamErrorCodeKey=61,
///     NSUnderlyingError=…
///
/// rendered verbatim, in red, at caption size, on the screen a person opens
/// at 7am. Note that the readable sentence is *inside* that dump: the fix
/// APIError itself wants is `error.localizedDescription` rather than
/// `\(error)`. `APIError` lives in `Core/` and is not this task's to change,
/// so this maps at the boundary instead. Flagged in the report.
///
/// This matches on the three prefixes `APIError.errorDescription` constructs,
/// which is a contract with a known producer in this repo rather than
/// scraping arbitrary text — and it falls through to a generic headline with
/// the raw string demoted to a quiet detail line, so an error nobody
/// anticipated is still legible and still reportable.
enum HomeErrorPresentation: Equatable {
    case readable(headline: String, detail: String?)

    /// Ordered: `AppState.fetchTodayOrGenerate` deliberately builds a 404
    /// whose body is a sentence written for the user ("Today's briefing
    /// hasn't been generated yet…"), so that one is promoted whole.
    private static let known: [(prefix: String, headline: String?)] = [
        ("Backend returned 404: ", nil),
        ("Network error: ", "Mercury couldn't reach the server."),
        ("Could not decode response: ", "Today's briefing arrived in a format this app can't read."),
    ]

    static func present(_ message: String) -> HomeErrorPresentation {
        for entry in known where message.hasPrefix(entry.prefix) {
            if let headline = entry.headline {
                // The tail is a debug dump. It goes to the console — AppState
                // and APIClient both print — and not onto the page.
                return .readable(headline: headline, detail: nil)
            }
            let body = String(message.dropFirst(entry.prefix.count))
            if !body.isEmpty { return .readable(headline: body, detail: nil) }
        }
        return .readable(
            headline: "Today's briefing didn't load.",
            detail: message.isEmpty ? nil : message
        )
    }

    var headline: String {
        if case .readable(let headline, _) = self { return headline }
        return ""
    }

    var detail: String? {
        if case .readable(_, let detail) = self { return detail }
        return nil
    }
}

#Preview {
    HomeView()
        .environment(AppState())
}
