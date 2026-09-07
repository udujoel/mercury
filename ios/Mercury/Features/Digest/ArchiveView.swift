import SwiftUI

/// The mornings still on the server.
///
/// A date list, not a calendar. A calendar implies you can pick any day and
/// most of them are empty — thirty rows of nothing around the handful that
/// exist. The list shows what is there.
///
/// **The horizon is read, not hardcoded.** ADR-011 sweeps briefings older
/// than `retention_days`, and `GET /briefings` returns whatever survived
/// that. Writing "30" here would be a second copy of a number that lives in
/// the sweeper, free to drift from it — and the drift is invisible until the
/// archive offers a day the sweeper already deleted.
struct ArchiveView: View {
    @Environment(\.dynamicTypeSize) private var typeSize

    let ids: [String]
    /// The briefing currently loaded, so the list can say which one you are
    /// already looking at rather than letting you tap it and see nothing
    /// change.
    let currentId: String?
    /// True when the last request FAILED, as distinct from returning
    /// nothing. Empty and unreachable look identical in `ids` and mean
    /// opposite things, and saying "nothing earlier yet" about a server you
    /// could not reach is a confident lie about the user's own data.
    var failed: Bool = false
    /// True while the first request is in flight. Without it the screen
    /// opens on "Nothing earlier yet" for as long as the round trip takes,
    /// then replaces it with a list — telling the user their briefings are
    /// gone and then taking it back.
    var loading: Bool = false
    /// True while a briefing is being opened. Distinct from `loading`,
    /// which is about the index request.
    var busy: Bool = false
    let onSelect: (String) -> Void
    /// Retry. The failure state tells you to pull down, so there has to be
    /// something to pull.
    var onRefresh: () async -> Void = {}

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if !ids.isEmpty {
                    list
                } else if loading {
                    ProgressView()
                        .tint(MercuryColor.accent.color)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, MercurySpace.section)
                } else if failed {
                    unreachable
                } else {
                    empty
                }
            }
            .mercuryGutter()
            .padding(.vertical, MercurySpace.comfortable)
        }
        .refreshable { await onRefresh() }
        .background(MercuryColor.surface.color.ignoresSafeArea())
        .navigationTitle("Earlier")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var list: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(ids, id: \.self) { id in
                row(id)
                if id != ids.last {
                    Rectangle().fill(MercuryColor.hairline.color).frame(height: 1)
                }
            }

            Text(horizon)
                .font(MercuryType.figureCaption)
                .foregroundStyle(MercuryColor.inkFaint.color)
                .padding(.top, MercurySpace.comfortable)
        }
    }

    private func row(_ id: String) -> some View {
        // Inert when it is the one already loaded, and while ANY load is in
        // flight. `openBriefing` returns immediately when one is running,
        // but the archive has already dismissed itself by then — so the tap
        // put the user back on Home with nothing loading and no explanation.
        Button { if isSelectable(id) { onSelect(id) } } label: {
            HStack(spacing: MercurySpace.snug) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(Self.display(id))
                        .font(MercuryType.cardBody)
                        .foregroundStyle(MercuryColor.ink.color)
                    if id == currentId {
                        Text("Loaded")
                            .font(MercuryType.eyebrow)
                            .textCase(.uppercase)
                            .tracking(0.6)
                            .foregroundStyle(MercuryColor.accent.color)
                    }
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.footnote)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .accessibilityHidden(true)
            }
            .frame(minHeight: 44)
            .padding(.vertical, MercurySpace.tight)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!isSelectable(id))
        .accessibilityElement(children: .combine)
        .accessibilityHint(hint(for: id))
    }

    private func isSelectable(_ id: String) -> Bool {
        id != currentId && !busy
    }

    private func hint(for id: String) -> String {
        if id == currentId { return "Already loaded" }
        if busy { return "Loading a briefing" }
        return "Loads this morning\u{2019}s briefing"
    }

    private var unreachable: some View {
        VStack(alignment: .leading, spacing: MercurySpace.snug) {
            Text("Could not reach the server")
                .font(MercuryType.cardTitle)
                .foregroundStyle(MercuryColor.ink.color)
            Text("Your earlier briefings are still there. Pull down to try again.")
                .font(MercuryType.cardBody)
                .foregroundStyle(MercuryColor.inkMuted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var empty: some View {
        VStack(alignment: .leading, spacing: MercurySpace.snug) {
            Text("Nothing earlier yet")
                .font(MercuryType.cardTitle)
                .foregroundStyle(MercuryColor.ink.color)
            Text("Briefings appear here the morning after they are made, and "
                 + "are removed automatically once they age out.")
                .font(MercuryType.cardBody)
                .foregroundStyle(MercuryColor.inkMuted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Says what the list is bounded by without naming a number the client
    /// does not own.
    private var horizon: String {
        "Older briefings are deleted automatically. This is everything still kept."
    }

    /// "Thursday 3 September" from a "2026-09-03" id.
    ///
    /// Falls back to the raw id rather than showing nothing: an id that does
    /// not parse is a bug worth seeing, and a blank row is a bug you cannot.
    static func display(_ id: String) -> String {
        // The DAY, then the time of day if the id carries one.
        //
        // Ids gained a `-HHMM` suffix so a day can hold several briefings,
        // and the whole string stopped parsing as a date — which the guard
        // below turns into a raw id on screen rather than a wrong one, but
        // "2026-09-07-1612" is not a label anyone wants to read.
        let day = String(id.prefix(10))
        guard let date = idFormatter().date(from: day) else { return id }

        let at = timeOfDay(in: id)

        // "Today" has to mean today ON THE SERVER, in the zone the id was
        // derived in — the same comparison the id itself was made with.
        // `Calendar.current` answers a different question, and answers it
        // wrongly by a whole day for anyone not in their configured zone.
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = BriefingClock.serverTimeZone
        // With four briefings a day, "Today" alone stops identifying one —
        // so the time is what tells them apart, and it is the only thing
        // that does.
        if calendar.isDateInToday(date) { return at.map { "Today, \($0)" } ?? "Today" }
        if calendar.isDateInYesterday(date) {
            return at.map { "Yesterday, \($0)" } ?? "Yesterday"
        }

        // The style's own `timeZone` symbol formats a zone NAME; setting the
        // zone the date is rendered IN is `.timeZone(_:)` on the style
        // itself, via `FormatStyle`'s environment.
        var style = Date.FormatStyle.dateTime.weekday(.wide).day().month(.wide)
        style.timeZone = BriefingClock.serverTimeZone
        let label = date.formatted(style)
        return at.map { "\(label), \($0)" } ?? label
    }

    /// "16:12" out of `2026-09-07-1612`, or nil for a day-only id.
    ///
    /// Formatted from the DIGITS rather than by building a `Date` and
    /// rendering it: the id's time is already in the server's zone, and
    /// round-tripping it through a `Date` is an invitation to shift it by
    /// one on the next timezone bug this project has. It has had five.
    static func timeOfDay(in id: String) -> String? {
        let suffix = id.dropFirst(10)
        guard suffix.count == 5, suffix.hasPrefix("-") else { return nil }
        let digits = suffix.dropFirst()
        guard digits.allSatisfy(\.isNumber) else { return nil }
        return "\(digits.prefix(2)):\(digits.suffix(2))"
    }

    /// Parsed in the SERVER's zone, which is where these ids come from.
    ///
    /// `BriefingClock` already holds that zone and self-corrects on first
    /// contact with the server; the first cut of this used `.current` and
    /// was right only for a user sitting in their configured timezone.
    ///
    /// Not a stored `let`: the zone can change under us when the client
    /// learns the server's, and a formatter built once at launch would keep
    /// the old one for the life of the process.
    ///
    /// Locale and calendar stay pinned — this parses a machine identifier,
    /// and a non-Gregorian calendar preference must not fail to read a
    /// Gregorian date.
    private static func idFormatter() -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = BriefingClock.serverTimeZone
        f.dateFormat = "yyyy-MM-dd"
        return f
    }
}

#Preview("Archive") {
    NavigationStack {
        ArchiveView(
            ids: ["2026-09-04", "2026-09-03", "2026-09-02", "2026-08-31"],
            currentId: "2026-09-04",
            onSelect: { _ in }
        )
    }
}

#Preview("Empty") {
    NavigationStack {
        ArchiveView(ids: [], currentId: nil, onSelect: { _ in })
    }
}
