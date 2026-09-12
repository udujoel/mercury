import Foundation

/// A briefing id, and the one question worth asking about one: which day?
///
/// **Why this file exists.** Until 2026-09-07 a briefing id WAS a day —
/// `2026-09-11` — so "the id" and "the day" were the same string and no code
/// ever had to tell them apart. PR #33 made a day hold four briefings, so ids
/// became `2026-09-11-2155` and the two concepts split.
///
/// The server split them properly: `briefing_day()` and `DAY_PREFIX_LENGTH`
/// in `backend/app/synthesis/storage.py`. The phone did not. It got one
/// open-coded `String(id.prefix(10))` in `ArchiveView` and, in two other
/// places, code that kept treating a day as an id — which still compiled,
/// still returned a perfectly valid string, and was silently wrong:
///
/// - the background prefetch asked R2 for `2026-09-12`, which has not existed
///   since the change, so every nightly prefetch 404ed for five days
/// - the pending rebuild was recorded under the day and compared for equality
///   against the timed id that landed, so it never matched
///
/// Neither line appeared in #33's diff. That is the point of naming the
/// concept: an unnamed idea cannot be grepped for, so the third site that
/// needs it is invisible until it breaks in production.
enum BriefingID {

    /// `2026-09-11` and `2026-09-11-2155` are both this day.
    ///
    /// Deliberately a prefix and not a parse. An id from a newer server may
    /// carry a suffix this build has never heard of, and the day is still the
    /// first ten characters; refusing to read it would lose a morning over a
    /// format note. The server takes the same view — `briefing_day` is
    /// `value[:DAY_PREFIX_LENGTH]` with no validation.
    static func day(of briefingID: String) -> String {
        String(briefingID.prefix(dayLength))
    }

    /// Do these two ids name the same morning?
    ///
    /// The comparison the rebuild state needs. A rebuild is asked for by DAY
    /// — the Worker dispatches `briefing_day` and the job picks the minute —
    /// so the briefing that lands can never be equal to the one requested,
    /// only same-day.
    static func sameDay(_ lhs: String, _ rhs: String) -> Bool {
        day(of: lhs) == day(of: rhs)
    }

    /// `yyyy-MM-dd`, the same ten characters the server slices.
    static let dayLength = 10
}
