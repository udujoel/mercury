import Foundation

/// What Home is allowed to say about this morning.
///
/// The screen this replaces made four claims — "64°", "Partly cloudy",
/// "San Francisco", "Priya Shah", "Invoice #4471", a 9am standup — and the
/// manifest supported none of them. They were `static let placeholder` arrays
/// behind defaulted initialisers, so `WeatherCard()` with no arguments
/// rendered a plausible and entirely invented morning. At 7am, on your own
/// phone, that is not a placeholder; it is a lie about your day.
///
/// So every number here is counted out of `BriefingManifest`, and where the
/// manifest cannot support a number the row says so in words. Pure, and
/// separated from the view, so `BriefingDigestTests` can check the counting
/// without rendering anything.
enum BriefingDigest {

    struct Row: Identifiable, Equatable {
        /// The segment id — `calendar`, `inbox`, `world`, `weather`.
        let id: String
        /// The label the BACKEND gave this segment. Not a client constant:
        /// the chapter ribbon in the player reads the same field, and the two
        /// screens naming the same audio differently is a bug waiting to
        /// happen the first time a segment is renamed server-side.
        let label: String
        /// A count, a length, or an empty state — never a fabricated detail.
        let detail: String
        /// Where tapping this row should seek to. `nil` when the briefing has
        /// no such segment, which is what makes the row non-interactive
        /// rather than a control that does nothing.
        let seekMs: Int?

        var isInBriefing: Bool { seekMs != nil }
    }

    /// The segments Home promises to account for every morning, in the order
    /// the backend emits them (`Segment` in `backend/app/core/schemas.py`).
    ///
    /// `cold_open` and `sign_off` are deliberately absent: they are how the
    /// briefing starts and stops, not things in it. `follow_ups` is absent
    /// too, but for the opposite reason — it appears only on days that have
    /// any, so promising a row for it and then explaining its absence every
    /// other morning would be noise.
    private static let standingOrder = ["weather", "calendar", "inbox", "world"]

    private static let framingSegments: Set<String> = ["cold_open", "sign_off"]

    /// What one attributed source *is*, per segment. Weather is absent on
    /// purpose — see `detail(for:)`.
    private static let nouns: [String: (one: String, many: String)] = [
        "calendar": ("event", "events"),
        "inbox": ("email", "emails"),
        "world": ("source", "sources"),
        "follow_ups": ("source", "sources"),
    ]

    /// What a morning with nothing in it actually says. An empty state is an
    /// invitation, not a gap to fill with fiction.
    private static let emptyStates: [String: String] = [
        "weather": "No forecast.",
        "calendar": "Nothing scheduled.",
        "inbox": "Inbox is quiet.",
        "world": "No news this morning.",
    ]

    // MARK: - Rows

    static func rows(for manifest: BriefingManifest?) -> [Row] {
        // No manifest is not the same as an empty briefing. Before anything
        // has been fetched the app knows nothing about this morning, and
        // "Nothing scheduled." would be a claim it has made no request to
        // support.
        guard let manifest else { return [] }

        let counts = attributedSourceCounts(in: manifest)
        let ordered = manifest.segments.sorted { $0.startMs < $1.startMs }

        var rows: [Row] = []
        var seen: Set<String> = []

        for (index, segment) in ordered.enumerated() where !framingSegments.contains(segment.id) {
            let end = index + 1 < ordered.count ? ordered[index + 1].startMs : manifest.durationMs
            rows.append(Row(
                id: segment.id,
                label: segment.label,
                detail: detail(for: segment.id,
                               count: counts[segment.id] ?? 0,
                               lengthMs: end - segment.startMs),
                seekMs: segment.startMs
            ))
            seen.insert(segment.id)
        }

        // Anything the briefing skipped is still worth a line — a calendar
        // row that simply vanishes on a clear day tells you nothing, whereas
        // "Nothing scheduled." is the answer to the question you opened the
        // app with.
        for id in standingOrder where !seen.contains(id) {
            rows.append(Row(id: id,
                            label: defaultLabel(for: id),
                            detail: emptyStates[id] ?? "Not in today's briefing.",
                            seekMs: nil))
        }

        return rows
    }

    /// The line under "Today's briefing": how long it runs, and how many
    /// distinct sources it draws on.
    static func summary(for manifest: BriefingManifest?) -> String? {
        guard let manifest else { return nil }
        let length = durationLabel(ms: manifest.durationMs)
        let sources = distinctSourceKeys(manifest.sources).count
        // Zero is the backend's "no grounding trail matched" warning, not a
        // fact about the briefing, so it is not rendered as "0 sources".
        guard sources > 0 else { return length }
        return "\(length) · \(sources) \(sources == 1 ? "source" : "sources")"
    }

    // MARK: - Detail

    private static func detail(for segmentID: String, count: Int, lengthMs: Int) -> String {
        guard let noun = nouns[segmentID], count > 0 else {
            // Two cases land here and both want the same answer.
            //
            // Weather always does: the backend builds SourceRefs from
            // emails, events and stories only (`_build_source_refs`), so a
            // weather segment has zero attributed sources on every briefing
            // ever generated. "No forecast." would be false — the briefing
            // has one, it just is not in the source trail.
            //
            // And any segment the fuzzy `_mentions` matcher failed to
            // attribute does too. The hosts talked about the inbox; saying
            // "Inbox is quiet." because the matcher missed would be a
            // different lie from the one this file exists to remove.
            //
            // So the row falls back to what it can prove: how long the hosts
            // spend there.
            return durationLabel(ms: lengthMs)
        }
        return "\(count) \(count == 1 ? noun.one : noun.many)"
    }

    /// Used only for a segment the briefing does not contain, which by
    /// definition carries no label of its own. Matches `SEGMENT_LABELS` in
    /// the backend so an absent row and a present one read the same.
    private static func defaultLabel(for id: String) -> String {
        switch id {
        case "weather":  return "Weather"
        case "calendar": return "Your day"
        case "inbox":    return "Your inbox"
        case "world":    return "Your world"
        default:         return id.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    // MARK: - Counting

    /// Distinct sources per segment.
    ///
    /// Two things make this more than a `Dictionary(grouping:)`.
    ///
    /// `_build_source_refs` emits one ref per (turn, item) pair, so an email
    /// the hosts return to later appears twice; counting refs would report
    /// four emails where three arrived. Distinctness is by title and sender
    /// together, since two newsletters can share a subject line.
    ///
    /// And a ref names a *turn*, not a segment, so the segment comes from the
    /// turn it points at — through `turn(atSourceIndex:)`, which indexes the
    /// compacted turn list and returns nil rather than trapping on an index a
    /// stale manifest made up.
    private static func attributedSourceCounts(in manifest: BriefingManifest) -> [String: Int] {
        var seenPerSegment: [String: Set<String>] = [:]
        for source in manifest.sources {
            guard let turn = manifest.turn(atSourceIndex: source.turnIndex) else { continue }
            seenPerSegment[turn.segment, default: []].insert(key(for: source))
        }
        return seenPerSegment.mapValues(\.count)
    }

    private static func distinctSourceKeys(_ sources: [SourceRef]) -> Set<String> {
        Set(sources.map(key(for:)))
    }

    /// Prefer the item's own id; fall back to title and sender.
    ///
    /// The fallback is what every briefing generated before `item_id` existed
    /// still gets, and it is wrong in one specific way: `_mentions` matches a
    /// calendar event by title, so two events called "1:1" on the same day
    /// produce two refs that this collapses into one, and the calendar row
    /// undercounts. Recurring meetings make that ordinary rather than exotic.
    /// New manifests carry the id and count both.
    ///
    /// A null byte, not a hyphen: a title is free text and can contain any
    /// separator a human would pick.
    private static func key(for source: SourceRef) -> String {
        if let id = source.itemId, !id.isEmpty { return id }
        return "\(source.title)\u{0}\(source.sourceName)"
    }

    // MARK: - Which day this briefing is for

    /// "Today's briefing", or "Thursday's briefing" when what is on the
    /// device is older than today.
    ///
    /// Keyed on `briefingId`, NOT on `generatedAt`. `generatedAt` is when the
    /// artifact was made, and ADR-001 makes it overnight: a briefing for the
    /// 4th with a 60-minute lead is generated at 23:30 on the 3rd, so
    /// comparing timestamps labels the morning's own briefing as yesterday's.
    /// `briefingId` is the date the briefing is FOR, derived in the same
    /// timezone the cron is scheduled in.
    ///
    /// `generatedAt` is still what names the day, because the weekday it
    /// wants is only in a `Date` — and for anything but today the two agree.
    static func title(briefingId: String?, generatedAt: Date?, now: Date = .now) -> String {
        // Same-DAY, not equal. This compared a briefing id against a bare
        // `yyyy-MM-dd` string, which was the same question until 2026-09-07
        // and has been a different one since: today's briefing is
        // `2026-09-13-0535`, the day is `2026-09-13`, they are never equal,
        // and so today's own briefing was titled "Sunday's briefing" every
        // single day.
        //
        // Found on the simulator, not by a test — the fourth site of this
        // bug, after the prefetch, the rebuild match and the offline
        // fallback. Each one compiled, each returned a valid string, and
        // each was quietly answering a question nobody had asked since the
        // id changed shape.
        guard let briefingId,
              !BriefingID.sameDay(briefingId, dayId.string(from: now)),
              let generatedAt
        else { return "Today's briefing" }
        return "\(generatedAt.formatted(.dateTime.weekday(.wide)))'s briefing"
    }

    /// Fixed locale and calendar: this formats a machine identifier, and a
    /// user on a non-Gregorian calendar would otherwise produce a string the
    /// backend never wrote.
    private static let dayId: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = .current
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    // MARK: - Lengths

    /// "6 min", or seconds below a minute so a 40-second segment does not
    /// round to "1 min" and read as if the hosts lingered.
    static func durationLabel(ms: Int) -> String {
        guard ms > 0 else { return "0 sec" }
        if ms < 60_000 { return "\(ms / 1000) sec" }
        let minutes = Int((Double(ms) / 60_000).rounded())
        return "\(minutes) min"
    }
}
