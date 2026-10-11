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
    /// What the user reads after pressing Rebuild.
    ///
    /// Says SUBMITTED and gives a number, because the previous version said
    /// "it takes a few minutes" while a spinner ran for twenty — and between
    /// a vague sentence and a spinning control, people believe the control.
    /// The estimate is measured: of the last ten production runs, seven took
    /// four minutes, two took three, one took seven.
    ///
    /// It also promises the arrival will announce itself, so nobody has to
    /// sit and poll the screen to find out.
    static var startedNote: String {
        "Request sent. Your new briefing usually takes about "
        + "\(AppState.rebuildEstimateMinutes) minutes — you can close the "
        + "app. It will be highlighted here when it arrives."
    }

    /// Ask for a rebuild, and say what happened.
    ///
    /// Named rather than written inline in the toolbar: the note it sets is
    /// rendered by the body below, so the button and its consequence were
    /// two places that had to agree.
    private func requestRebuild() {
        Task {
            do {
                _ = try await appState.regenerateToday()
                regenerateNote = Self.startedNote
            } catch {
                // Through the same presenter the load errors use. Without it
                // a transport failure printed "Network error: Error
                // Domain=NSURLErrorDomain…" onto the page, under a masthead.
                regenerateNote = HomeErrorPresentation
                    .present(error.localizedDescription)
                    .sentence
            }
        }
    }

    private var rows: [BriefingDigest.Row] { BriefingDigest.rows(for: appState.manifest) }

    /// The exception path, and ONLY it.
    ///
    /// Two conditions, both required. The load must have failed with the
    /// backend's "not generated yet" 404 — not a timeout, not a decode
    /// failure, not a 500, all of which are ordinary errors with ordinary
    /// error text. And there must be no manifest on screen: a restored
    /// briefing from an earlier morning is something to play, so telling
    /// someone there is nothing while a briefing sits under their thumb
    /// would be both wrong and rude.
    private var isWaiting: Bool {
        guard appState.manifest == nil,
              case .failed = appState.loadState
        else { return false }
        return WaitingRoom.shouldEnter(status: appState.todaysFailureStatus)
    }

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

                    // Task 19, asked for twice. The archive has held this
                    // since task 10; it lived behind a button at the BOTTOM
                    // of the page labelled "Earlier briefings", which is a
                    // place people reach by deciding to look for something.
                    if !calendarDays.isEmpty {
                        CalendarStripView(
                            days: calendarDays,
                            selectedDay: appState.manifest
                                .map { BriefingID.day(of: $0.briefingId) },
                            onSelect: openDay)
                            .padding(.top, MercurySpace.snug)
                    }

                    Text("Good morning")
                        .font(MercuryType.greeting)
                        .foregroundStyle(MercuryColor.ink.color)
                        .padding(.top, MercurySpace.generous)
                        .padding(.bottom, MercurySpace.generous)
                        // The date is already in the masthead directly above;
                        // VoiceOver reading it twice in a row is noise.
                        .accessibilityAddTraits(.isHeader)

                    // The waiting room REPLACES the card, rather than
                    // sitting under it. Two boxes both explaining that
                    // nothing is ready is one box too many, and the card's
                    // play button on that state is a button that cannot
                    // work.
                    if isWaiting {
                        WaitingRoomCard(
                            onRetry: { Task { await appState.playBriefing() } })
                    } else {
                    TodaysBriefingCard(
                        title: briefingTitle,
                        state: appState.loadState,
                        summary: BriefingDigest.summary(for: appState.manifest),
                        // The briefing on SCREEN, not just "something is
                        // playing": a browsed day's card offers to play it.
                        isPlaying: AppState.cardShowsPlaying(
                            isPlaying: appState.playback.isPlaying,
                            isCurrent: appState.isPlaybackCurrent),
                        // Drawn in the day's accent until it has been
                        // looked at. The rebuild's whole point is that the
                        // briefing changed, so arriving silently would be
                        // the same as not rebuilding.
                        isFresh: appState.hasUnseenRebuild,
                        // Task 33. Read off the manifest ON SCREEN, so a
                        // restored or browsed morning says what IT lacked.
                        notice: BriefingHealth.notice(
                            for: appState.manifest?.degradedSources ?? [],
                            narratorFallback: appState.manifest?.narratorFallback ?? false),
                        onPlay: play,
                        // NOT `play`. Retry follows a failed fetch, and
                        // `togglePlayback` would pause or resume whatever
                        // audio happens to be loaded instead of trying the
                        // fetch again — which is the one thing the button
                        // says it does.
                        onRetry: { Task { await appState.playBriefing() } }
                    )
                    }

                    // The desk: the stories themselves. Above the rows
                    // below, which summarise the same morning by segment.
                    // `deskStories` rather than the digest directly: the
                    // cards must belong to the briefing on screen, or their
                    // turn offsets address a manifest that is not loaded.
                    if !deskStories.isEmpty {
                        // Follows the briefing on screen, not the clock.
                        // The card above already says "Friday's briefing"
                        // via `BriefingDigest.title`; a desk headed "TODAY'S"
                        // under it is the same confident wrongness that
                        // rule about the card exists to prevent — and the
                        // calendar strip makes it reachable in one tap
                        // rather than only on a stale restore.
                        SectionLabel(deskLabel)
                            .padding(.top, MercurySpace.section)
                            .padding(.bottom, MercurySpace.snug)

                        DeskFeed(stories: deskStories) { selectedStory = $0 }
                    }

                    // What several outlets independently thought worth
                    // covering, above what one sender emailed. A stronger
                    // claim on attention, so it comes first.
                    if !corroborated.isEmpty {
                        SectionLabel("More than one outlet")
                            .padding(.top, MercurySpace.section)
                            .padding(.bottom, MercurySpace.snug)

                        CorroboratedSection(
                            items: corroborated,
                            summary: CorroboratedStories.summary(corroborated),
                            onSelect: { selectedStory = $0 })
                    }

                    // What arrived, under what was made of it. The
                    // backend has shipped this list since PR #17 and no
                    // view has ever read it.
                    if !newsletterSenders.isEmpty {
                        SectionLabel("The newsletters")
                            .padding(.top, MercurySpace.section)
                            .padding(.bottom, MercurySpace.snug)

                        NewsletterSection(
                            senders: newsletterSenders,
                            summary: NewsletterDesk.summary(
                                for: appState.digest?.newsletters ?? []),
                            onOpen: openStory(id:))
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
                // The briefing IN THE ENGINE, which is not the one on screen
                // while another day is being browsed (ROADMAP task 49).
                if let manifest = appState.playback.loadedManifest ?? appState.manifest {
                    MiniPlayerBar(
                        manifest: manifest,
                        playback: appState.playback,
                        onOpen: {
                            appState.showPlaying()
                            showPlayer = true
                        }
                    )
                }
            }
            .navigationDestination(isPresented: $showPlayer) {
                PlayerView()
            }
            .toolbar {
                // Beside the gear rather than at the foot of the page.
                //
                // It lived below the desk, which meant scrolling past a
                // dozen stories to reach the control for "this is stale" —
                // the exact moment you are least willing to scroll. Two
                // items, and the destructive one is not the one your thumb
                // lands on by default.
                ToolbarItem(placement: .topBarTrailing) {
                    RegenerateButton(
                        isRunning: appState.isRegenerating,
                        remaining: appState.regeneration?.remaining,
                        style: .toolbar,
                        onConfirm: requestRebuild
                    )
                }
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
            // The calendar strip needs the archive, and until now the only
            // thing that loaded it was the archive SHEET's own `.task` — so
            // a strip on Home would have stayed empty until someone opened
            // the screen it was meant to replace.
            //
            // Guarded rather than unconditional: `loadArchive` is a network
            // round trip, this fires on every appearance of Home, and the
            // list changes at most four times a day.
            .task {
                guard appState.archive.isEmpty, !appState.archiveLoading else { return }
                await appState.loadArchive()
            }
            // Covers playback started outside a direct tap on the card — the
            // wake notification's "Play" action or tapping the notification
            // itself (PRD F-17), which drive `AppState.playPrefetched`
            // directly rather than going through this view's handler.
            .onChange(of: appState.playback.isPlaying) { _, isPlaying in
                if isPlaying { showPlayer = true }
            }
            // The edge back. This observer had only ever pointed one way —
            // into the player — so the sign-off left the listener sitting on
            // a dead player screen with a finished briefing, and getting to
            // the morning's stories meant knowing to tap Back.
            //
            // Keyed on `hasFinished` rather than on `!isPlaying`, and the
            // distinction is the whole point: `isPlaying` goes false on
            // every pause, and yanking someone out of the player because
            // they paused to listen to something else would be a worse bug
            // than the one being fixed. `hasFinished` is set in exactly one
            // place — `PlaybackCoordinator.finish()` — and means the audio
            // reached its end.
            .onChange(of: appState.playback.hasFinished) { _, finished in
                if finished { showPlayer = false }
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
        // Seen. Not on appear, and not on a timer: the highlight says "this
        // is not the briefing you already heard", and the moment that stops
        // being news is the moment the listener acts on it.
        appState.markRebuildSeen()
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

    /// "Today's desk", or "Friday's desk" when browsing another day.
    ///
    /// Derived from the same `briefingTitle` the card uses, so the two
    /// cannot disagree: it is "Today's briefing" or "Friday's briefing", and
    /// this swaps the noun.
    private var deskLabel: String {
        briefingTitle.replacingOccurrences(of: "briefing", with: "desk")
    }

    /// The last seven days, with whichever briefing each one has.
    ///
    /// Built from `appState.archive`, which `.task` below loads once. Empty
    /// until it arrives, and the strip simply does not render — better than
    /// seven dead cells that become live a moment later.
    private var calendarDays: [CalendarStrip.Day] {
        guard !appState.archive.isEmpty else { return [] }
        return CalendarStrip.days(archive: appState.archive, today: .now)
    }

    /// Open that day's briefing, and its desk with it.
    ///
    /// `openBriefing` already replaces the manifest AND the digest, so the
    /// stories, the newsletters and the outlet section all follow the date
    /// without this having to say so — they are all derived from whichever
    /// briefing is loaded, and guarded on its id.
    private func openDay(_ day: CalendarStrip.Day) {
        guard let id = day.briefingId else { return }
        // Selecting a day is looking at a briefing, so any rebuild highlight
        // has served its purpose.
        appState.markRebuildSeen()
        // NOT auto-played. Tapping a date is looking at a morning, not
        // choosing something to listen to — the play button is right there
        // if that is what was meant. The archive SHEET still auto-plays,
        // because picking from a list of briefings is choosing one.
        Task { await appState.openBriefing(id: id, andPlay: false) }
    }

    /// Stories two or more distinct outlets carried.
    ///
    /// Counted from `sources` here rather than read from `sourceCount`,
    /// deliberately: every briefing already in R2 was generated before that
    /// field was fixed to count outlets instead of cluster members, so a
    /// restored morning would otherwise show a survey company that emailed
    /// four times as the best-corroborated story of the day.
    private var corroborated: [CorroboratedStories.Item] {
        CorroboratedStories.items(from: deskStories)
    }

    /// The senders whose newsletters arrived for the briefing on screen.
    ///
    /// Guarded on the same `briefingId` match as `deskStories`: a digest
    /// from a different morning would list newsletters that have nothing to
    /// do with the briefing loaded, and the story ids it joins on would
    /// address cards that are not there.
    private var newsletterSenders: [NewsletterDesk.Sender] {
        guard let digest = appState.digest,
              digest.briefingId == appState.manifest?.briefingId
        else { return [] }
        return NewsletterDesk.senders(from: digest.newsletters)
    }

    /// Opens the story a newsletter contributed to, when the desk has it.
    ///
    /// Silently does nothing when the id names a story this digest does not
    /// carry — an `is_digest_story` decision can keep a cluster off the
    /// browse surface while a newsletter still records having fed it, and
    /// presenting an empty detail sheet would be worse than not opening one.
    private func openStory(id: String) {
        selectedStory = deskStories.first { $0.id == id }
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

    /// Both halves, for the places that have one line rather than a card.
    /// `TodaysBriefingCard` sets them as separate views; the rebuild note
    /// under the button is a single `Text`.
    var sentence: String {
        guard let detail else { return headline }
        return "\(headline)\n\(detail)"
    }
}

#Preview {
    HomeView()
        .environment(AppState())
}
