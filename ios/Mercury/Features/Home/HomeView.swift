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

    private var rows: [BriefingDigest.Row] { BriefingDigest.rows(for: appState.manifest) }

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
                        state: appState.loadState,
                        summary: BriefingDigest.summary(for: appState.manifest),
                        isPlaying: appState.playback.isPlaying,
                        onPlay: play,
                        onRetry: play
                    )

                    if !rows.isEmpty {
                        SectionLabel("In today's briefing")
                            .padding(.top, MercurySpace.section)
                            .padding(.bottom, MercurySpace.snug)

                        BriefingRows(rows: rows, onSelect: open(at:))
                    }
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
            // Covers playback started outside a direct tap on the card — the
            // wake notification's "Play" action or tapping the notification
            // itself (PRD F-17), which drive `AppState.playPrefetched`
            // directly rather than going through this view's handler.
            .onChange(of: appState.playback.isPlaying) { _, isPlaying in
                if isPlaying { showPlayer = true }
            }
        }
    }

    private func play() {
        Task {
            await appState.playBriefing()
            if case .ready = appState.loadState { showPlayer = true }
        }
    }

    /// Tapping a row goes to the player *at that chapter*. If the briefing is
    /// not loaded into the coordinator yet the seek is harmless — `seek` on a
    /// coordinator with no file schedules nothing — and the play path takes
    /// over from the card.
    private func open(at row: BriefingDigest.Row) {
        guard let ms = row.seekMs else { return }
        appState.playback.seek(toMs: ms)
        showPlayer = true
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
