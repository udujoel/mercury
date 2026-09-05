import Foundation

/// The browse surface: every newsletter that arrived, and every story that
/// clustered — including the ones the ranker never gave to the hosts.
///
/// A SIBLING of the manifest, not part of it. The manifest is what the player
/// downloads to start playing, and folding ~100KB of browse data into it would
/// make every launch pay for a screen the user may never open (ADR-013).
///
/// Every field here is decoded leniently for one reason: the digest shipped
/// before the app could read it, so briefings already in R2 have digests
/// without `points` or `image_url`, and briefings older than that have no
/// digest at all. A missing field is an empty state, never a decode failure —
/// the same rule `Turn` follows for its timings.
/// `Decodable`, not `Codable`: `schema_version` is a coding key the client
/// reads and has no stored property for, so there is nothing to synthesise
/// an encoder from — and nothing in the app writes a digest anyway. The
/// artifact is produced by the backend and only ever read here.
struct Digest: Decodable, Equatable {
    /// The newest shape this build understands. Matches
    /// `DIGEST_SCHEMA_VERSION` in `app/core/schemas.py`.
    static let supportedSchemaVersion = 1

    let briefingId: String
    let generatedAt: Date?
    let stories: [DigestStory]
    let newsletters: [DigestNewsletter]

    enum CodingKeys: String, CodingKey {
        case briefingId = "briefing_id"
        case generatedAt = "generated_at"
        case stories, newsletters
        case schemaVersion = "schema_version"
    }

    /// Thrown for a digest this build is too old to read.
    struct UnsupportedSchema: Error, LocalizedError {
        let version: Int
        var errorDescription: String? {
            "digest schema version \(version) is newer than this build supports"
        }
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)

        // Checked FIRST, and it is the one field that refuses rather than
        // degrades. Every other default here turns a missing field into an
        // empty state, which is right for a digest written by an OLDER
        // backend — but applying the same leniency to a NEWER one renders
        // an empty desk for a briefing that has stories, and says nothing.
        // Absent means version 1, which is what the field's own default is.
        let version = try c.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        guard version <= Self.supportedSchemaVersion else {
            throw UnsupportedSchema(version: version)
        }

        briefingId = try c.decodeIfPresent(String.self, forKey: .briefingId) ?? ""
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        stories = try c.decodeIfPresent([DigestStory].self, forKey: .stories) ?? []
        newsletters = try c.decodeIfPresent(
            [DigestNewsletter].self, forKey: .newsletters) ?? []
    }

    init(briefingId: String, generatedAt: Date? = nil,
         stories: [DigestStory] = [], newsletters: [DigestNewsletter] = []) {
        self.briefingId = briefingId
        self.generatedAt = generatedAt
        self.stories = stories
        self.newsletters = newsletters
    }

    /// Stories the hosts actually discussed, in the order they discussed
    /// them. What the desk leads with, because a story you can hear is worth
    /// more on this screen than one you can only read.
    var covered: [DigestStory] {
        stories.filter(\.coveredInAudio).sorted {
            ($0.turnIndex ?? .max) < ($1.turnIndex ?? .max)
        }
    }

    /// Everything the briefing had no time for. Not lesser — just unread.
    var uncovered: [DigestStory] {
        stories.filter { !$0.coveredInAudio }
    }
}

struct DigestStory: Codable, Equatable, Hashable, Identifiable {
    let id: String
    let headline: String
    let summary: String?
    /// The summary as points. Empty for an unenriched story and for every
    /// digest generated before bullets existed — the detail view falls back
    /// to the paragraph, and to nothing when there is no paragraph either.
    let points: [String]
    let whyItMatters: String?
    /// The publisher's lead image, as a URL. Null is ordinary: a
    /// newsletter-only story has none at all, because rule 5 discards the
    /// body its images live in.
    let imageURL: URL?
    let topics: [String]
    let sourceCount: Int
    let sources: [DigestSource]
    let coveredInAudio: Bool
    /// Indexes `manifest.turns` after compaction. Null unless covered.
    let turnIndex: Int?
    let segment: String?

    enum CodingKeys: String, CodingKey {
        case id, headline, summary, points, topics, sources, segment
        case whyItMatters = "why_it_matters"
        case imageURL = "image_url"
        case sourceCount = "source_count"
        case coveredInAudio = "covered_in_audio"
        case turnIndex = "turn_index"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        headline = try c.decode(String.self, forKey: .headline)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        points = try c.decodeIfPresent([String].self, forKey: .points) ?? []
        whyItMatters = try c.decodeIfPresent(String.self, forKey: .whyItMatters)
        // A malformed URL is no image, not a broken story. `URL(string:)`
        // returning nil is the same outcome as the field being absent.
        imageURL = (try c.decodeIfPresent(String.self, forKey: .imageURL))
            .flatMap(URL.init(string:))
        topics = try c.decodeIfPresent([String].self, forKey: .topics) ?? []
        sources = try c.decodeIfPresent([DigestSource].self, forKey: .sources) ?? []
        // Denormalised on the backend, but derived here when absent so the
        // two can never disagree by more than the backend being wrong.
        sourceCount = try c.decodeIfPresent(Int.self, forKey: .sourceCount) ?? sources.count
        coveredInAudio = try c.decodeIfPresent(Bool.self, forKey: .coveredInAudio) ?? false
        turnIndex = try c.decodeIfPresent(Int.self, forKey: .turnIndex)
        segment = try c.decodeIfPresent(String.self, forKey: .segment)
    }

    init(id: String, headline: String, summary: String? = nil, points: [String] = [],
         whyItMatters: String? = nil, imageURL: URL? = nil, topics: [String] = [],
         sourceCount: Int? = nil, sources: [DigestSource] = [],
         coveredInAudio: Bool = false, turnIndex: Int? = nil, segment: String? = nil) {
        self.id = id
        self.headline = headline
        self.summary = summary
        self.points = points
        self.whyItMatters = whyItMatters
        self.imageURL = imageURL
        self.topics = topics
        self.sourceCount = sourceCount ?? sources.count
        self.sources = sources
        self.coveredInAudio = coveredInAudio
        self.turnIndex = turnIndex
        self.segment = segment
    }
}

struct DigestSource: Codable, Equatable, Hashable, Identifiable {
    let sourceName: String
    let title: String
    /// Null for an email, structurally: `gmail.py` builds every email `Item`
    /// with no URL, because a message in your inbox has no public address
    /// and the "view in browser" link lives in a body rule 5 discards.
    /// A newsletter-only story therefore has NO openable source at all,
    /// which the detail view has to say rather than render as a dead row.
    let url: URL?
    let publishedAt: Date?
    let sourceType: String

    var id: String { "\(sourceName)\u{0}\(title)" }

    var isOpenable: Bool { url != nil }

    enum CodingKeys: String, CodingKey {
        case title, url
        case sourceName = "source_name"
        case publishedAt = "published_at"
        case sourceType = "source_type"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sourceName = try c.decodeIfPresent(String.self, forKey: .sourceName) ?? "Unknown"
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        url = (try c.decodeIfPresent(String.self, forKey: .url)).flatMap(URL.init(string:))
        publishedAt = try c.decodeIfPresent(Date.self, forKey: .publishedAt)
        sourceType = try c.decodeIfPresent(String.self, forKey: .sourceType) ?? "rss"
    }

    init(sourceName: String, title: String, url: URL? = nil,
         publishedAt: Date? = nil, sourceType: String = "rss") {
        self.sourceName = sourceName
        self.title = title
        self.url = url
        self.publishedAt = publishedAt
        self.sourceType = sourceType
    }
}

struct DigestNewsletter: Codable, Equatable, Hashable, Identifiable {
    let id: String
    let sourceName: String
    let sender: String?
    let subject: String
    let receivedAt: Date?
    /// The stories this newsletter contributed to. The join back onto
    /// `Digest.stories`.
    let storyIds: [String]
    /// Whether anything from this newsletter reached the audio.
    ///
    /// The wire field is `in_briefing`, a BOOL. The first version of this
    /// model invented a `turn_index` that the backend has never emitted, so
    /// it decoded as nil for every newsletter and the list reported that
    /// none of them made the briefing — silently wrong in exactly the
    /// direction the screen exists to show.
    let inBriefing: Bool

    var reachedTheBriefing: Bool { inBriefing }

    /// Always nil today: `gmail.py` builds every email Item with no URL,
    /// because a message in your inbox has no public address. Carried
    /// because the backend carries it, and documented there for the same
    /// reason — so nobody builds a tappable row expecting one.
    let url: URL?

    enum CodingKeys: String, CodingKey {
        case id, subject, sender, url
        case sourceName = "source_name"
        case receivedAt = "received_at"
        case storyIds = "story_ids"
        case inBriefing = "in_briefing"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        sourceName = try c.decodeIfPresent(String.self, forKey: .sourceName) ?? "Unknown"
        sender = try c.decodeIfPresent(String.self, forKey: .sender)
        subject = try c.decodeIfPresent(String.self, forKey: .subject) ?? ""
        receivedAt = try c.decodeIfPresent(Date.self, forKey: .receivedAt)
        storyIds = try c.decodeIfPresent([String].self, forKey: .storyIds) ?? []
        inBriefing = try c.decodeIfPresent(Bool.self, forKey: .inBriefing) ?? false
        url = (try c.decodeIfPresent(String.self, forKey: .url)).flatMap(URL.init(string:))
    }

    init(id: String, sourceName: String, subject: String, sender: String? = nil,
         receivedAt: Date? = nil, storyIds: [String] = [],
         inBriefing: Bool = false, url: URL? = nil) {
        self.id = id
        self.sourceName = sourceName
        self.sender = sender
        self.subject = subject
        self.receivedAt = receivedAt
        self.storyIds = storyIds
        self.inBriefing = inBriefing
        self.url = url
    }
}

/// What `GET /briefings` returns: the mornings still in the bucket.
struct BriefingIndex: Codable, Equatable {
    let briefings: [String]

    enum CodingKeys: String, CodingKey { case briefings }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        briefings = try c.decodeIfPresent([String].self, forKey: .briefings) ?? []
    }

    init(briefings: [String]) { self.briefings = briefings }
}
