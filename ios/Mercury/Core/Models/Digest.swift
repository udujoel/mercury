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
struct Digest: Codable, Equatable {
    let briefingId: String
    let generatedAt: Date?
    let stories: [DigestStory]
    let newsletters: [DigestNewsletter]

    enum CodingKeys: String, CodingKey {
        case briefingId = "briefing_id"
        case generatedAt = "generated_at"
        case stories, newsletters
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
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

struct DigestNewsletter: Codable, Equatable, Identifiable {
    let id: String
    let sourceName: String
    let subject: String
    let receivedAt: Date?
    /// Nil when this newsletter's story never made the briefing — which is
    /// most of them on a busy morning, and the thing this screen exists to
    /// make visible.
    let turnIndex: Int?

    var reachedTheBriefing: Bool { turnIndex != nil }

    enum CodingKeys: String, CodingKey {
        case id, subject
        case sourceName = "source_name"
        case receivedAt = "received_at"
        case turnIndex = "turn_index"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        sourceName = try c.decodeIfPresent(String.self, forKey: .sourceName) ?? "Unknown"
        subject = try c.decodeIfPresent(String.self, forKey: .subject) ?? ""
        receivedAt = try c.decodeIfPresent(Date.self, forKey: .receivedAt)
        turnIndex = try c.decodeIfPresent(Int.self, forKey: .turnIndex)
    }

    init(id: String, sourceName: String, subject: String,
         receivedAt: Date? = nil, turnIndex: Int? = nil) {
        self.id = id
        self.sourceName = sourceName
        self.subject = subject
        self.receivedAt = receivedAt
        self.turnIndex = turnIndex
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
