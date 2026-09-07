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

    /// Mirrors `MUSIC_SETS` in `app/core/schemas.py` and the directories
    /// under `assets/music/`. "none" is a real choice rather than an
    /// absence: someone who wants no music should be able to say so, not
    /// have it mean "use the default".
    static let musicSets: [(id: String, label: String, note: String)] = [
        ("paper", "Paper", "Felt piano and upright bass. Warm, unhurried."),
        ("broadsheet", "Broadsheet", "Muted strings and marimba. Crisper."),
        ("first-light", "First Light", "Ambient pad. Very quiet, for early."),
        ("none", "None", "No music at all."),
    ]

    var briefingLength: String?

    /// Segments the listener has turned off. ROADMAP task 15.
    ///
    /// This was here once and was REMOVED, because the script chain
    /// required every canonical segment to be present — so it stored a
    /// preference nothing read, the exact failure this artifact exists to
    /// prevent. It is back only now that `resolve_segments` narrows the
    /// prompt, both response schemas, the draft's repair check and the
    /// revision's validator from this one list.
    var disabledSegments: [String]

    /// The segments that can be turned off, in the order the briefing runs.
    ///
    /// `cold_open` and `sign_off` are absent on purpose and match
    /// `STRUCTURAL_SEGMENTS` on the server: a briefing with no greeting
    /// starts mid-sentence and one with no close just stops. Offering a
    /// toggle the job would override is the same lie as a toggle nothing
    /// reads.
    static let optionalSegments: [(id: String, label: String, note: String)] = [
        ("weather", "Weather", "Conditions and the day's forecast."),
        ("calendar", "Your day", "The day walked through, with prep flagged."),
        ("inbox", "Your inbox", "Top emails, and newsletters as a group."),
        ("world", "Your world", "Clustered news from your feeds."),
        ("follow_ups", "Follow-ups", "Where you owe someone a reply."),
    ]

    var ttsProvider: String?
    /// Which music set tops and tails the briefing. One of `musicSets`, or
    /// nil for the server's default.
    var musicSet: String?
    var voiceA: String?
    var voiceB: String?
    /// Empty means the curated defaults, not "no news". An empty list is what
    /// an app that has never touched this produces, and a briefing with no
    /// news is not a thing anyone asks for by accident.
    var feeds: [String]
    var mutedSenders: [String]

    init(briefingLength: String? = nil,
         ttsProvider: String? = nil, musicSet: String? = nil,
         voiceA: String? = nil, voiceB: String? = nil,
         feeds: [String] = [], mutedSenders: [String] = [],
         disabledSegments: [String] = []) {
        self.disabledSegments = disabledSegments
        self.briefingLength = briefingLength
        self.musicSet = musicSet
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
        case ttsProvider = "tts_provider"
        case musicSet = "music_set"
        case voiceA = "voice_a"
        case voiceB = "voice_b"
        case feeds
        case mutedSenders = "muted_senders"
        case disabledSegments = "disabled_segments"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        briefingLength = try c.decodeIfPresent(String.self, forKey: .briefingLength)
        ttsProvider = try c.decodeIfPresent(String.self, forKey: .ttsProvider)
        musicSet = try c.decodeIfPresent(String.self, forKey: .musicSet)
        voiceA = try c.decodeIfPresent(String.self, forKey: .voiceA)
        voiceB = try c.decodeIfPresent(String.self, forKey: .voiceB)
        feeds = try c.decodeIfPresent([String].self, forKey: .feeds) ?? []
        mutedSenders = try c.decodeIfPresent([String].self, forKey: .mutedSenders) ?? []
        disabledSegments =
            try c.decodeIfPresent([String].self, forKey: .disabledSegments) ?? []
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
        try c.encodeIfPresent(ttsProvider, forKey: .ttsProvider)
        try c.encodeIfPresent(musicSet, forKey: .musicSet)
        try c.encodeIfPresent(voiceA, forKey: .voiceA)
        try c.encodeIfPresent(voiceB, forKey: .voiceB)
        try c.encode(feeds, forKey: .feeds)
        try c.encode(mutedSenders, forKey: .mutedSenders)
        try c.encode(disabledSegments, forKey: .disabledSegments)
    }

}
