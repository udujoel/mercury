import Foundation

/// What the app can change about how a briefing is MADE.
///
/// The distinction that matters: settings on this screen fall into two kinds,
/// and only one of them can live on the phone.
///
/// Wake time, appearance and playback rate affect the device, so
/// `UserDefaults` is the right home — the cron does not care what time your
/// phone rings. Length, voices, feeds and segments affect the JOB, which runs
/// at 04:00 on a machine that cannot read your phone's defaults. Those have to
/// be an artifact the job reads, or the control only appears to work.
///
/// Every field is optional, and null means "use the server's environment".
/// That is what lets the artifact be absent entirely — the state every
/// deployment starts in, and the one this must never break.
struct UserSettings: Codable, Equatable {
    static let schemaVersion = 1

    var briefingLength: String?
    /// Segment ids to SKIP.
    ///
    /// An exclusion rather than an inclusion so a segment added in a later
    /// release is on by default. An allow-list would silently hide every new
    /// segment from anyone who had ever opened this screen.
    var disabledSegments: [String]
    var ttsProvider: String?
    var voiceA: String?
    var voiceB: String?
    /// Empty means the curated defaults, not "no news". An empty list is what
    /// an app that has never touched this produces, and a briefing with no
    /// news is not a thing anyone asks for by accident.
    var feeds: [String]
    var mutedSenders: [String]

    init(briefingLength: String? = nil, disabledSegments: [String] = [],
         ttsProvider: String? = nil, voiceA: String? = nil, voiceB: String? = nil,
         feeds: [String] = [], mutedSenders: [String] = []) {
        self.briefingLength = briefingLength
        self.disabledSegments = disabledSegments
        self.ttsProvider = ttsProvider
        self.voiceA = voiceA
        self.voiceB = voiceB
        self.feeds = feeds
        self.mutedSenders = mutedSenders
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case updatedAt = "updated_at"
        case briefingLength = "briefing_length"
        case disabledSegments = "disabled_segments"
        case ttsProvider = "tts_provider"
        case voiceA = "voice_a"
        case voiceB = "voice_b"
        case feeds
        case mutedSenders = "muted_senders"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        briefingLength = try c.decodeIfPresent(String.self, forKey: .briefingLength)
        disabledSegments =
            try c.decodeIfPresent([String].self, forKey: .disabledSegments) ?? []
        ttsProvider = try c.decodeIfPresent(String.self, forKey: .ttsProvider)
        voiceA = try c.decodeIfPresent(String.self, forKey: .voiceA)
        voiceB = try c.decodeIfPresent(String.self, forKey: .voiceB)
        feeds = try c.decodeIfPresent([String].self, forKey: .feeds) ?? []
        mutedSenders = try c.decodeIfPresent([String].self, forKey: .mutedSenders) ?? []
    }

    /// Writes the version and a timestamp the client never reads back.
    ///
    /// They are for the OTHER readers: the generation job logs which settings
    /// it applied, and "when did this last change" is the first question
    /// anyone asks when a briefing comes out wrong.
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.schemaVersion, forKey: .schemaVersion)
        try c.encode(ISO8601DateFormatter().string(from: .now), forKey: .updatedAt)
        try c.encodeIfPresent(briefingLength, forKey: .briefingLength)
        try c.encode(disabledSegments, forKey: .disabledSegments)
        try c.encodeIfPresent(ttsProvider, forKey: .ttsProvider)
        try c.encodeIfPresent(voiceA, forKey: .voiceA)
        try c.encodeIfPresent(voiceB, forKey: .voiceB)
        try c.encode(feeds, forKey: .feeds)
        try c.encode(mutedSenders, forKey: .mutedSenders)
    }

    /// The segments a briefing can contain, in the order they are spoken.
    ///
    /// Mirrors `Segment` in `app/core/schemas.py`. Two of them are not
    /// offered: the cold open and the sign-off are the shape of the show
    /// rather than content, and a briefing that starts mid-sentence and stops
    /// dead is not a shorter briefing, it is a broken one.
    static let optionalSegments: [(id: String, label: String)] = [
        ("weather", "Weather"),
        ("calendar", "Your day"),
        ("inbox", "Your inbox"),
        ("world", "Your world"),
    ]
}
