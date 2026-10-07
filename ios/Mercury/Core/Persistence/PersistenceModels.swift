import Foundation
import SwiftData

/// SwiftData persistence, per PRD 3.7.
///
/// Named `*Entity` (rather than `Turn`/`SourceRef` as in the PRD pseudocode)
/// to avoid colliding with the Codable wire types in
/// `Core/Models/BriefingManifest.swift` that the networking layer and
/// `PlaybackCoordinator` already use for `Turn`/`SourceRef`/`BriefingSegment`.
/// One pair of types is the API contract, the other is on-disk storage;
/// keeping them distinct also means a manifest schema change never silently
/// breaks persistence, and vice versa.
@Model
final class BriefingEntity {
    @Attribute(.unique) var id: String            // "2026-08-21"
    var generatedAt: Date
    var durationMs: Int
    var localAudioPath: String?                   // nil until downloaded
    var remoteAudioURL: URL
    var lastPositionMs: Int                        // resume point
    var completed: Bool
    /// `BriefingManifest.degradedSources`. Optional for the same reason as
    /// `SourceRefEntity.itemId`: a store written by an earlier build has no
    /// column for it, and optional is what makes that a lightweight
    /// migration. Without it, the one notice that says a morning is not
    /// what you configured would vanish on every relaunch and offline open.
    var degradedSources: [String]?

    @Relationship(deleteRule: .cascade) var segments: [SegmentEntity]
    @Relationship(deleteRule: .cascade) var turns: [TurnEntity]
    @Relationship(deleteRule: .cascade) var sources: [SourceRefEntity]

    init(
        id: String,
        generatedAt: Date,
        durationMs: Int,
        remoteAudioURL: URL,
        localAudioPath: String? = nil,
        lastPositionMs: Int = 0,
        completed: Bool = false,
        degradedSources: [String]? = nil,
        segments: [SegmentEntity] = [],
        turns: [TurnEntity] = [],
        sources: [SourceRefEntity] = []
    ) {
        self.id = id
        self.generatedAt = generatedAt
        self.durationMs = durationMs
        self.remoteAudioURL = remoteAudioURL
        self.localAudioPath = localAudioPath
        self.lastPositionMs = lastPositionMs
        self.completed = completed
        self.degradedSources = degradedSources
        self.segments = segments
        self.turns = turns
        self.sources = sources
    }
}

@Model
final class SegmentEntity {
    var segmentID: String
    var label: String
    var startMs: Int

    init(segmentID: String, label: String, startMs: Int) {
        self.segmentID = segmentID
        self.label = label
        self.startMs = startMs
    }
}

@Model
final class TurnEntity {
    var startMs: Int
    var endMs: Int
    var speaker: String        // "A" | "B"
    var segmentID: String
    var text: String

    init(startMs: Int, endMs: Int, speaker: String, segmentID: String, text: String) {
        self.startMs = startMs
        self.endMs = endMs
        self.speaker = speaker
        self.segmentID = segmentID
        self.text = text
    }
}

@Model
final class SourceRefEntity {
    var turnIndex: Int
    var title: String
    var url: URL?
    var sourceName: String
    var sourceType: String
    /// Optional, which is also what makes this a lightweight migration: a
    /// store written by an earlier build has no column for it and reads back
    /// as nil rather than failing to open.
    var itemId: String?
    var clusterSize: Int       // how many outlets carried it

    init(turnIndex: Int, title: String, url: URL?, sourceName: String,
         sourceType: String, clusterSize: Int, itemId: String? = nil) {
        self.turnIndex = turnIndex
        self.title = title
        self.url = url
        self.sourceName = sourceName
        self.sourceType = sourceType
        self.itemId = itemId
        self.clusterSize = clusterSize
    }
}
