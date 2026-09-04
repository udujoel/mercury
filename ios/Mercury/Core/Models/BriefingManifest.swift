import Foundation

/// One speaker turn in the generated script.
///
/// Mirrors `backend/app/core/schemas.py::Turn`. `id` is a computed property
/// (not part of the JSON). Decoding is hand-written only to tolerate null
/// timings — see `startMs`.
struct Turn: Codable, Identifiable, Equatable, Hashable {
    let speaker: String
    let segment: String
    let text: String
    // Decoded leniently. The backend now drops un-synthesised turns from the
    // manifest, so these should always be present — but a turn whose TTS
    // failed carries null timing in the Python schema (`int | None`), and
    // modelling them as non-optional here meant ONE such turn failed the
    // entire briefing with "Expected value of type Int but found null".
    // A briefing missing a line beats a briefing that will not decode.
    let startMs: Int
    let endMs: Int

    // Writing init(from:) suppresses the synthesized memberwise init, which
    // previews and tests construct Turns with. Declare it explicitly.
    init(speaker: String, segment: String, text: String, startMs: Int, endMs: Int) {
        self.speaker = speaker
        self.segment = segment
        self.text = text
        self.startMs = startMs
        self.endMs = endMs
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        speaker = try c.decode(String.self, forKey: .speaker)
        segment = try c.decode(String.self, forKey: .segment)
        text = try c.decode(String.self, forKey: .text)
        startMs = try c.decodeIfPresent(Int.self, forKey: .startMs) ?? -1
        endMs = try c.decodeIfPresent(Int.self, forKey: .endMs) ?? -1
    }

    /// A turn that never made it into the audio. Filtered out on load.
    var isRendered: Bool { startMs >= 0 && endMs > startMs }

    enum CodingKeys: String, CodingKey {
        case speaker, segment, text
        case startMs = "start_ms"
        case endMs = "end_ms"
    }

    /// Stable within a single manifest: two turns never share both a start
    /// offset and a speaker, so this is sufficient for SwiftUI identity and
    /// for the `currentTurn?.id` comparison in `PlaybackCoordinator`.
    var id: String { "\(startMs)-\(endMs)-\(speaker)" }
}

/// A chapter marker: `{id, label, start_ms}` in the manifest JSON.
struct BriefingSegment: Codable, Identifiable, Equatable, Hashable {
    let id: String
    let label: String
    let startMs: Int

    enum CodingKeys: String, CodingKey {
        case id, label
        case startMs = "start_ms"
    }
}

/// Attribution for a factual claim made in a turn.
struct SourceRef: Codable, Identifiable, Equatable, Hashable {
    let turnIndex: Int
    let title: String
    let url: String?
    let sourceName: String
    let sourceType: String
    let clusterSize: Int
    /// The id of the item this ref points at, when the manifest carries one.
    ///
    /// Optional because every briefing already in R2 was generated before the
    /// field existed, and those must keep decoding.
    let itemId: String?

    enum CodingKeys: String, CodingKey {
        case turnIndex = "turn_index"
        case title, url
        case sourceName = "source_name"
        case sourceType = "source_type"
        case clusterSize = "cluster_size"
        case itemId = "item_id"
    }

    /// Written out rather than synthesised so `itemId` can default.
    ///
    /// Every manifest already in R2 predates the field, and so does every
    /// fixture and every test that builds a ref by hand; a memberwise
    /// initialiser would have made all of them say `itemId: nil` to express
    /// nothing.
    init(turnIndex: Int, title: String, url: String?, sourceName: String,
         sourceType: String, clusterSize: Int, itemId: String? = nil) {
        self.turnIndex = turnIndex
        self.title = title
        self.url = url
        self.sourceName = sourceName
        self.sourceType = sourceType
        self.clusterSize = clusterSize
        self.itemId = itemId
    }

    var id: String { "\(turnIndex)-\(title)" }
}

/// The single artifact the iOS client consumes. Mirrors
/// `backend/app/core/schemas.py::BriefingManifest` exactly.
///
/// The backend may emit WAV rather than m4a when ffmpeg is absent, so
/// `audioURL`'s extension must never be assumed — always read it from
/// `audioUrl`, never hardcoded.
struct BriefingManifest: Codable, Equatable {
    let briefingId: String
    let generatedAt: Date
    let durationMs: Int
    let audioUrl: String
    let segments: [BriefingSegment]
    let turns: [Turn]
    let sources: [SourceRef]

    /// Turns that actually exist in the audio, computed ONCE.
    ///
    /// The backend excludes un-synthesised turns, so this is normally just
    /// `turns`; the filter is the client-side half of that guarantee, so a
    /// stale or hand-edited manifest cannot desync the transcript.
    ///
    /// It used to be a computed property — `turns.filter(\.isRendered)` —
    /// and `turn(containing:)` called it on entry. The binary search inside
    /// was O(log n), but it was searching an array that had just been
    /// rebuilt in O(n), allocating a fresh copy of every turn in the
    /// briefing twenty times a second for the whole six minutes. Storing it
    /// makes the O(log n) claim in that method's doc comment true for the
    /// first time.
    ///
    /// Not part of the wire format: excluded from `CodingKeys`, derived in
    /// both initialisers.
    let renderedTurns: [Turn]

    enum CodingKeys: String, CodingKey {
        case briefingId = "briefing_id"
        case generatedAt = "generated_at"
        case durationMs = "duration_ms"
        case audioUrl = "audio_url"
        case segments, turns, sources
    }

    init(
        briefingId: String,
        generatedAt: Date,
        durationMs: Int,
        audioUrl: String,
        segments: [BriefingSegment],
        turns: [Turn],
        sources: [SourceRef]
    ) {
        self.briefingId = briefingId
        self.generatedAt = generatedAt
        self.durationMs = durationMs
        self.audioUrl = audioUrl
        self.segments = segments
        self.turns = turns
        self.sources = sources
        self.renderedTurns = turns.filter(\.isRendered)
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            briefingId: try c.decode(String.self, forKey: .briefingId),
            generatedAt: try c.decode(Date.self, forKey: .generatedAt),
            durationMs: try c.decode(Int.self, forKey: .durationMs),
            audioUrl: try c.decode(String.self, forKey: .audioUrl),
            segments: try c.decode([BriefingSegment].self, forKey: .segments),
            turns: try c.decode([Turn].self, forKey: .turns),
            sources: try c.decode([SourceRef].self, forKey: .sources)
        )
    }

    var audioURL: URL? { URL(string: audioUrl) }
}

// MARK: - Transcript sync helpers

extension BriefingManifest {

    /// Binary search over `turns` (sorted by `startMs`) for the turn whose
    /// half-open interval `[startMs, endMs)` contains `ms`.
    ///
    /// Returns `nil` for a position that falls in a gap between turns (no
    /// turn covers it), and for a position before the first turn or after
    /// the last. O(log n), safe to call from the 20 Hz playback tick.
    func turn(containing ms: Int) -> Turn? {
        let turns = renderedTurns
        guard !turns.isEmpty else { return nil }

        var lo = 0
        var hi = turns.count - 1

        while lo <= hi {
            let mid = (lo + hi) / 2
            let candidate = turns[mid]

            if ms < candidate.startMs {
                hi = mid - 1
            } else if ms >= candidate.endMs {
                lo = mid + 1
            } else {
                return candidate
            }
        }
        return nil
    }

    /// The first rendered turn that has not finished by `ms` — the one
    /// covering it, or failing that the next one along.
    ///
    /// Named for what it does rather than for the search it runs. It was
    /// `turn(startingAtOrAfter:)`, which promised a turn whose `startMs` is
    /// at least `ms` and does not deliver one: the search is a lower bound
    /// on `endMs`, so a position INSIDE a turn returns that turn, whose
    /// `startMs` is behind `ms`. Every caller wanted the covering-or-next
    /// behaviour; only the name disagreed.
    ///
    /// Used on the SEEK path only. `turn(containing:)` returns nil in the
    /// silence between turns, which is right during playback — the
    /// transcript holds the line that just finished. It is wrong
    /// immediately after a seek: landing in a gap would otherwise leave the
    /// transcript showing a line from somewhere else in the briefing
    /// entirely. This resolves such a position forward to the line about to
    /// be spoken.
    ///
    /// Same O(log n) binary search, on the same pre-computed array: find
    /// the first turn whose `endMs` is past `ms`.
    func turn(coveringOrAfter ms: Int) -> Turn? {
        let turns = renderedTurns
        var lo = 0
        var hi = turns.count

        while lo < hi {
            let mid = (lo + hi) / 2
            if turns[mid].endMs <= ms {
                lo = mid + 1
            } else {
                hi = mid
            }
        }
        return lo < turns.count ? turns[lo] : nil
    }

    /// The chapter covering an arbitrary playback position: the segment with
    /// the greatest `startMs` not exceeding `ms`.
    func segment(containing ms: Int) -> BriefingSegment? {
        segments
            .filter { $0.startMs <= ms }
            .max(by: { $0.startMs < $1.startMs })
    }

    /// The chapter that owns a given turn: the segment with the greatest
    /// `startMs` not exceeding the turn's `startMs`.
    func segment(for turn: Turn) -> BriefingSegment? {
        segment(containing: turn.startMs)
    }

    /// The playback offset to seek to when a chapter tab is tapped.
    func segmentStartMs(id: String) -> Int? {
        segments.first(where: { $0.id == id })?.startMs
    }

    /// Resolves a `SourceRef.turnIndex` to the turn it refers to, for the
    /// show notes tap-to-seek behaviour (PRD F-19).
    ///
    /// The backend guarantees `turn_index` is valid against the manifest's
    /// *compacted* turn list — i.e. `renderedTurns`, not the raw `turns`
    /// array, which may still contain un-synthesised turns with sentinel
    /// timing (see `Turn.isRendered`). Guaranteed today is not the same as
    /// safe forever: a stale cached manifest, a hand-edited fixture, or a
    /// future backend regression could still hand this an index the client
    /// has never seen. Out of range returns `nil` rather than trapping.
    func turn(atSourceIndex index: Int) -> Turn? {
        let turns = renderedTurns
        guard turns.indices.contains(index) else { return nil }
        return turns[index]
    }
}


/// Mirrors `BriefingLength` in backend/app/core/schemas.py. Raw values must
/// match exactly — they cross the wire as the `length` field of the generate
/// request, and the backend maps them to LENGTH_BUDGETS word counts.
enum BriefingLength: String, Codable, CaseIterable, Identifiable, Sendable {
    case express, standard, full

    var id: String { rawValue }

    var label: String {
        switch self {
        case .express: return "Express (~3 min)"
        case .standard: return "Standard (~7 min)"
        case .full: return "Full (~15 min)"
        }
    }
}
