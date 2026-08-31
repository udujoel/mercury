import Foundation
import SwiftData

/// Persists briefings, restores resume position, and enforces retention:
/// keep 7 briefings, delete audio files older than 7 days but keep
/// transcripts and source refs (they're tiny and make history searchable).
@ModelActor
actor BriefingStore {

    static let retainedBriefingCount = 7
    static let audioRetentionDays = 7

    /// Converts a decoded wire manifest into on-disk entities and upserts it.
    func save(_ manifest: BriefingManifest, localAudioPath: String?) throws {
        let descriptor = FetchDescriptor<BriefingEntity>(
            predicate: #Predicate { $0.id == manifest.briefingId }
        )
        let existing = try modelContext.fetch(descriptor).first

        let segments = manifest.segments.map {
            SegmentEntity(segmentID: $0.id, label: $0.label, startMs: $0.startMs)
        }
        let turns = manifest.turns.map {
            TurnEntity(startMs: $0.startMs, endMs: $0.endMs, speaker: $0.speaker,
                       segmentID: $0.segment, text: $0.text)
        }
        let sources = manifest.sources.map {
            SourceRefEntity(turnIndex: $0.turnIndex, title: $0.title,
                            url: $0.url.flatMap(URL.init(string:)),
                            sourceName: $0.sourceName, sourceType: $0.sourceType,
                            clusterSize: $0.clusterSize)
        }

        if let existing {
            // A briefing id is a DATE, so a regeneration reuses it — that is
            // exactly what the backend's `--force` / `--briefing-id` path
            // does when a bad briefing is replaced. The id matching
            // therefore says nothing about the content being the same, and
            // carrying the old resume point across dropped the listener
            // four minutes into a briefing they had never heard. A carried
            // `completed` was worse: `startPlayback` treats it as already
            // heard, so the replacement was skipped entirely.
            //
            // `generatedAt` is the version stamp; `durationMs` catches the
            // case of a backend that reuses one. Both are compared BEFORE
            // either is overwritten.
            let isReplacement = existing.generatedAt != manifest.generatedAt
                || existing.durationMs != manifest.durationMs

            existing.generatedAt = manifest.generatedAt
            existing.durationMs = manifest.durationMs
            existing.remoteAudioURL = manifest.audioURL ?? existing.remoteAudioURL
            existing.localAudioPath = localAudioPath ?? existing.localAudioPath
            existing.segments = segments
            existing.turns = turns
            existing.sources = sources

            // Conditional, not unconditional: `AppState.playBriefing` saves
            // the SAME manifest twice per playback — once when it arrives,
            // once with the local audio path — so resetting on every save
            // would wipe the resume point continuously and break the
            // feature this is here to provide.
            if isReplacement {
                existing.lastPositionMs = 0
                existing.completed = false
            }
        } else {
            let entity = BriefingEntity(
                id: manifest.briefingId,
                generatedAt: manifest.generatedAt,
                durationMs: manifest.durationMs,
                remoteAudioURL: manifest.audioURL ?? URL(string: "about:blank")!,
                localAudioPath: localAudioPath,
                segments: segments,
                turns: turns,
                sources: sources
            )
            modelContext.insert(entity)
        }

        try modelContext.save()
        try enforceRetention()
    }

    /// Round-trips through the model context: `lastPositionMs` written here
    /// must be readable back after a fresh fetch, which is what
    /// `resumePositionMs(for:)` verifies.
    func updateResumePosition(briefingId: String, positionMs: Int) throws {
        let descriptor = FetchDescriptor<BriefingEntity>(
            predicate: #Predicate { $0.id == briefingId }
        )
        guard let entity = try modelContext.fetch(descriptor).first else { return }
        entity.lastPositionMs = positionMs
        try modelContext.save()
    }

    func resumePositionMs(for briefingId: String) throws -> Int? {
        let descriptor = FetchDescriptor<BriefingEntity>(
            predicate: #Predicate { $0.id == briefingId }
        )
        return try modelContext.fetch(descriptor).first?.lastPositionMs
    }

    func markCompleted(briefingId: String) throws {
        let descriptor = FetchDescriptor<BriefingEntity>(
            predicate: #Predicate { $0.id == briefingId }
        )
        guard let entity = try modelContext.fetch(descriptor).first else { return }
        entity.completed = true
        try modelContext.save()
    }

    func allBriefings() throws -> [BriefingEntity] {
        let descriptor = FetchDescriptor<BriefingEntity>(
            sortBy: [SortDescriptor(\.generatedAt, order: .reverse)]
        )
        return try modelContext.fetch(descriptor)
    }

    // MARK: - Retention

    private func enforceRetention() throws {
        let all = try allBriefings()

        // Keep 7 briefings; delete the rest entirely (cascade removes
        // turns/segments/sources).
        if all.count > Self.retainedBriefingCount {
            for stale in all[Self.retainedBriefingCount...] {
                modelContext.delete(stale)
            }
        }

        // Among the retained set, drop local audio for anything older than
        // 7 days but keep the transcript and source refs.
        let cutoff = Calendar.current.date(
            byAdding: .day, value: -Self.audioRetentionDays, to: Date()
        ) ?? .distantPast

        for briefing in all.prefix(Self.retainedBriefingCount) where briefing.generatedAt < cutoff {
            if let path = briefing.localAudioPath {
                try? FileManager.default.removeItem(atPath: path)
            }
            briefing.localAudioPath = nil
        }

        try modelContext.save()
    }
}
