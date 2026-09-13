import SwiftUI

/// A week across the top of Home. Tap a day to go back to that morning.
///
/// Roadmap task 19, asked for twice. The archive has held this since task 10
/// — `/briefings` lists every id the server still has — but it lived behind
/// a button labelled "Earlier briefings" at the bottom of the page, which is
/// a place people reach by deciding to look for something. A strip at the top
/// is a place they reach by arriving.
///
/// **Days with nothing are not tappable.** The roadmap is specific about it:
/// "days with no briefing are visibly empty rather than tappable-and-dead".
/// A control that does nothing teaches people the app is broken; a date drawn
/// plainly with no affordance teaches them nothing happened that day, which
/// is true.
enum CalendarStrip {

    /// One day in the strip.
    struct Day: Identifiable, Equatable {
        /// `yyyy-MM-dd`, the same shape `BriefingID.day(of:)` returns, so the
        /// join onto the archive needs no parsing.
        let id: String
        let date: Date
        /// The briefing to open. Nil when the server has none for this day —
        /// swept by retention, or a morning the cron never ran.
        let briefingId: String?
        let isToday: Bool

        var hasBriefing: Bool { briefingId != nil }
    }

    /// Seven days ending today.
    ///
    /// Ending rather than centred: Mercury is a morning product and there is
    /// nothing to show for tomorrow, so half a strip would always be empty.
    static let dayCount = 7

    /// Build the strip from the archive index.
    ///
    /// - Parameters:
    ///   - archive: every briefing id the server holds, any shape — legacy
    ///     `yyyy-MM-dd` or timed `yyyy-MM-dd-HHmm`.
    ///   - today: today in the SERVER's zone, which is what `AppDelegate`
    ///     already derives. Using the device zone here would put a day on
    ///     screen the server does not agree exists.
    static func days(archive: [String], today: Date,
                     calendar: Calendar = .autoupdatingCurrent,
                     timeZone: TimeZone = BriefingClock.serverTimeZone) -> [Day] {
        var cal = calendar
        cal.timeZone = timeZone

        // Newest first within a day, so `first` is the one to open — the
        // same choice `briefingToPrefetch` makes, for the same reason: four
        // runs a day means "that day's briefing" is the latest of them.
        var newestPerDay: [String: String] = [:]
        for id in archive {
            let day = BriefingID.day(of: id)
            if let existing = newestPerDay[day], existing >= id { continue }
            newestPerDay[day] = id
        }

        let todayKey = Self.key(for: today, calendar: cal)
        return (0..<dayCount).reversed().compactMap { back in
            guard let date = cal.date(byAdding: .day, value: -back, to: today)
            else { return nil }
            let key = Self.key(for: date, calendar: cal)
            return Day(id: key, date: date,
                       briefingId: newestPerDay[key], isToday: key == todayKey)
        }
    }

    /// `yyyy-MM-dd` in the given calendar's zone.
    ///
    /// Built from components rather than a `DateFormatter`, so it cannot
    /// drift from `AppDelegate.briefingId(for:)` through a locale: that one
    /// pins `en_US_POSIX` precisely because a formatter is a locale away
    /// from producing Buddhist-calendar years.
    static func key(for date: Date, calendar: Calendar) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    /// "M", "T", "W" — one letter, because seven of them have to fit.
    static func initial(for date: Date, calendar: Calendar = .autoupdatingCurrent,
                        locale: Locale = .autoupdatingCurrent) -> String {
        let weekday = calendar.component(.weekday, from: date)
        var symbols = calendar.veryShortWeekdaySymbols
        if symbols.count == 7 {
            return symbols[(weekday - 1) % 7]
        }
        // A calendar without seven symbols is not a case worth trusting a
        // subscript to.
        symbols = Calendar(identifier: .gregorian).veryShortWeekdaySymbols
        return symbols.indices.contains(weekday - 1) ? symbols[weekday - 1] : "?"
    }
}

/// The strip itself.
struct CalendarStripView: View {
    let days: [CalendarStrip.Day]
    let selectedDay: String?
    let onSelect: (CalendarStrip.Day) -> Void

    var body: some View {
        HStack(spacing: 0) {
            ForEach(days) { day in
                dayCell(day)
                    .frame(maxWidth: .infinity)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("The last seven days")
    }

    @ViewBuilder
    private func dayCell(_ day: CalendarStrip.Day) -> some View {
        let selected = day.id == selectedDay
        let content = VStack(spacing: MercurySpace.tight) {
            Text(CalendarStrip.initial(for: day.date))
                .font(MercuryType.eyebrow)
                .foregroundStyle(MercuryColor.inkFaint.color)
            Text("\(Calendar.autoupdatingCurrent.component(.day, from: day.date))")
                .font(MercuryType.figureCaption)
                .foregroundStyle(
                    day.hasBriefing
                        ? (selected ? MercuryColor.inkOnAccent.color
                                    : MercuryColor.ink.color)
                        // Drawn, not hidden. An empty day is information.
                        : MercuryColor.inkFaint.color)
                .frame(width: 28, height: 28)
                .background {
                    if selected {
                        Circle().fill(MercuryColor.accent.color)
                    } else if day.isToday {
                        Circle().strokeBorder(
                            MercuryColor.hairline.color, lineWidth: 1)
                    }
                }
        }
        .frame(minHeight: 44)
        .contentShape(Rectangle())

        if day.hasBriefing {
            Button { onSelect(day) } label: { content }
                .buttonStyle(.plain)
                .accessibilityLabel(Self.label(day))
                .accessibilityAddTraits(selected ? [.isSelected] : [])
        } else {
            // Not a disabled Button: a greyed-out control reads as a fault,
            // and "no briefing that day" is not a fault.
            content
                .accessibilityLabel(Self.label(day))
        }
    }

    static func label(_ day: CalendarStrip.Day) -> String {
        let when = day.date.formatted(.dateTime.weekday(.wide).day().month(.wide))
        return day.hasBriefing ? when : "\(when), no briefing"
    }
}
