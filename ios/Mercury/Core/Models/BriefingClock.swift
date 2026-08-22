import Foundation

/// The timezone briefing ids are named in.
///
/// A `briefing_id` is a `YYYY-MM-DD` string the SERVER derives from its
/// configured `USER_TIMEZONE`. The client derives the same string locally so
/// the background task can know what to prefetch without a round trip — but
/// it has to derive it in the server's zone, not the device's.
///
/// Using `.current` meant the two disagreed the moment the device left the
/// configured zone. In Lisbon-configured Mercury opened at 20:00 in New York,
/// it is already the next day in Lisbon: the server has filed 2026-08-23
/// while the device asks for 2026-08-22, the prefetch 404s, and the app
/// silently falls back to a foreground fetch — defeating the layer entirely,
/// with no error anywhere.
///
/// The zone is learned from `GET /health` and cached, because the background
/// task must be able to compute an id with no network at all.
enum BriefingClock {

    private static let key = "mercury.serverTimeZoneIdentifier"

    /// The server's zone if we have ever learned it, else the device's.
    /// Falling back to `.current` is right for a first launch that has not
    /// reached the server yet: it is correct for the common case where the
    /// user is in their configured zone, and self-corrects on first contact.
    static var serverTimeZone: TimeZone {
        guard let identifier = UserDefaults.standard.string(forKey: key),
              let zone = TimeZone(identifier: identifier)
        else { return .current }
        return zone
    }

    static func remember(identifier: String) {
        guard TimeZone(identifier: identifier) != nil else {
            print("[Mercury] server reported unusable timezone \(identifier)")
            return
        }
        UserDefaults.standard.set(identifier, forKey: key)
    }
}
