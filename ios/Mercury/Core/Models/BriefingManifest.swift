import Foundation

/// One speaker turn in the generated script.
///
/// Mirrors `backend/app/core/schemas.py::Turn`. `id` is a computed property
/// (not part of the JSON) so decoding stays a plain `Codable` synthesis —
/// no custom `init(from:)` needed.
struct Turn: Codable, Identifiable, Equatable, Hashable {
    let speaker: String
    let segment: String
    let text: String
    let startMs: Int
    let endMs: Int

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

    enum CodingKeys: String, CodingKey {
        case turnIndex = "turn_index"
        case title, url
        case sourceName = "source_name"
        case sourceType = "source_type"
        case clusterSize = "cluster_size"
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

    enum CodingKeys: String, CodingKey {
        case briefingId = "briefing_id"
        case generatedAt = "generated_at"
        case durationMs = "duration_ms"
        case audioUrl = "audio_url"
        case segments, turns, sources
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

    /// The chapter that owns a given turn: the segment with the greatest
    /// `startMs` not exceeding the turn's `startMs`.
    func segment(for turn: Turn) -> BriefingSegment? {
        segments
            .filter { $0.startMs <= turn.startMs }
            .max(by: { $0.startMs < $1.startMs })
    }

    /// The playback offset to seek to when a chapter tab is tapped.
    func segmentStartMs(id: String) -> Int? {
        segments.first(where: { $0.id == id })?.startMs
    }
}
