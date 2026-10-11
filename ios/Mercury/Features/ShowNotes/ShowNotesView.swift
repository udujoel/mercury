import SwiftUI

/// PRD F-19: "Post-briefing source list with sender, headline, one-line
/// summary and a deep link to the original. Every item mentioned in audio
/// has a corresponding tappable entry."
///
/// Grouping and the turn_index→seek mapping are split out into
/// `ShowNotesLogic` (below) so they're testable independent of SwiftUI —
/// see `MercuryTests/ShowNotesTests.swift`.
struct ShowNotesView: View {
    let manifest: BriefingManifest
    /// Seeks playback to a given offset. Injected rather than reaching for
    /// `PlaybackCoordinator` directly so this view previews and tests
    /// without a live audio engine.
    let onSeek: (Int) -> Void

    private var groups: [ShowNotesLogic.Group] {
        ShowNotesLogic.grouped(manifest.sources)
    }

    var body: some View {
        List {
            ForEach(groups) { group in
                Section(group.label) {
                    ForEach(group.sources) { source in
                        ShowNotesRow(source: source) {
                            if let ms = ShowNotesLogic.seekTargetMs(for: source, in: manifest) {
                                onSeek(ms)
                            }
                            // Out of range: defensively do nothing rather
                            // than seek somewhere wrong or crash. See
                            // `BriefingManifest.turn(atSourceIndex:)`.
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Show notes")
        .scrollContentBackground(.hidden)
        .background(MercuryColor.surface.color.ignoresSafeArea())
    }
}

private struct ShowNotesRow: View {
    let source: SourceRef
    let onTapToSeek: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(source.title)
                    .font(MercuryType.chapterTab)
                    .foregroundStyle(MercuryColor.ink.color)
                HStack(spacing: 6) {
                    Text(source.sourceName)
                    if source.clusterSize > 1 {
                        Text("· \(source.clusterSize) outlets")
                    }
                }
                .font(MercuryType.caption)
                .foregroundStyle(MercuryColor.inkMuted.color)
            }

            Spacer()

            if let urlString = source.url, let url = URL(string: urlString) {
                Link(destination: url) {
                    Image(systemName: "arrow.up.right.square")
                        .foregroundStyle(MercuryColor.inkMuted.color)
                }
                .buttonStyle(.plain)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: onTapToSeek)
        .listRowBackground(MercuryColor.surfaceRaised.color)
    }
}

// MARK: - Logic (SwiftUI-independent, unit tested)

enum ShowNotesLogic {

    struct Group: Identifiable {
        /// The underlying `sourceType` (e.g. "email", "calendar", "news").
        let id: String
        let label: String
        let sources: [SourceRef]
    }

    /// Groups sources by `sourceType`, preserving first-seen order of each
    /// group (NOT alphabetical, and NOT `Dictionary`'s undefined iteration
    /// order) so the section list matches the order stories were actually
    /// discussed in the briefing.
    ///
    /// One row per SOURCE, not per citation. The manifest carries one ref
    /// for every turn that cites a source, so an event the hosts mentioned
    /// four times arrived as four identical rows (ROADMAP task 50). The
    /// first mention is kept, so tapping a row seeks to where it is first
    /// discussed.
    static func grouped(_ sources: [SourceRef]) -> [Group] {
        var order: [String] = []
        var buckets: [String: [SourceRef]] = [:]
        var seen: Set<String> = []

        // Input order is turn order (the backend builds refs turn by turn),
        // so the first occurrence IS the first mention.
        for source in sources {
            guard seen.insert(identity(of: source)).inserted else { continue }
            if buckets[source.sourceType] == nil {
                buckets[source.sourceType] = []
                order.append(source.sourceType)
            }
            buckets[source.sourceType]?.append(source)
        }

        return order.map { type in
            Group(id: type, label: label(for: type), sources: buckets[type] ?? [])
        }
    }

    /// What makes two refs the same source. The item id when the manifest
    /// carries one; otherwise everything the row shows, so two outlets that
    /// happen to share a headline stay two rows.
    private static func identity(of source: SourceRef) -> String {
        if let itemId = source.itemId { return "id:\(itemId)" }
        return [source.sourceType, source.sourceName, source.title, source.url ?? ""]
            .joined(separator: "\u{1F}")
    }

    /// A section header in words. `sourceType.capitalized` put RSS items
    /// under "Rss".
    static func label(for sourceType: String) -> String {
        switch sourceType {
        case "rss", "news": return "News"
        default: return sourceType.capitalized
        }
    }

    /// The playback offset (ms) to seek to when a show notes row is tapped,
    /// resolved via `BriefingManifest.turn(atSourceIndex:)`. `nil` when
    /// `source.turnIndex` doesn't resolve to a turn — an out-of-range index
    /// from a stale manifest or backend regression — so callers can no-op
    /// instead of seeking to a wrong or crashing position.
    static func seekTargetMs(for source: SourceRef, in manifest: BriefingManifest) -> Int? {
        manifest.turn(atSourceIndex: source.turnIndex)?.startMs
    }
}

#Preview {
    NavigationStack {
        if let manifest = try? ManifestPreviewFixture.load() {
            ShowNotesView(manifest: manifest, onSeek: { _ in })
        } else {
            Text("No fixture")
        }
    }
}

/// Preview-only fixture loader. `MercuryTests` has its own copy
/// (`ManifestDecodingTests.loadFixture`) that this deliberately does NOT
/// depend on — app target previews must build without linking the test
/// target.
private enum ManifestPreviewFixture {
    static func load() throws -> BriefingManifest {
        let json = """
        {
          "briefing_id": "2026-08-21",
          "generated_at": "2026-08-21T07:00:00Z",
          "duration_ms": 26000,
          "audio_url": "https://cdn.example.com/briefings/2026-08-21/audio.wav",
          "segments": [
            { "id": "cold_open", "label": "Good morning", "start_ms": 0 }
          ],
          "turns": [
            { "speaker": "A", "segment": "cold_open", "text": "Good morning.", "start_ms": 0, "end_ms": 2000 }
          ],
          "sources": [
            { "turn_index": 0, "title": "Standup", "url": null, "source_name": "Google Calendar", "source_type": "calendar", "cluster_size": 1 },
            { "turn_index": 0, "title": "Q3 planning doc", "url": "https://mail.google.com/mail/thread/abc123", "source_name": "Priya Shah", "source_type": "email", "cluster_size": 1 }
          ]
        }
        """.data(using: .utf8)!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(BriefingManifest.self, from: json)
    }
}
