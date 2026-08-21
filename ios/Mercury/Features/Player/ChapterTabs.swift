import SwiftUI

/// Player Zone 2 (middle): horizontally scrolling chapter tabs. Active tab
/// is a filled pill, inactive tabs sit at 45% opacity. Tapping a tab seeks
/// to that segment's start offset.
struct ChapterTabs: View {
    let segments: [BriefingSegment]
    let activeSegmentID: String?
    let onSelect: (BriefingSegment) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 10) {
                ForEach(segments) { segment in
                    let isActive = segment.id == activeSegmentID
                    Text(segment.label)
                        .font(MercuryType.chapterTab)
                        .foregroundStyle(isActive ? .black : MercuryColor.textPrimary)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(
                            Capsule().fill(isActive ? MercuryColor.textPrimary : Color.white.opacity(0.12))
                        )
                        .opacity(isActive ? 1.0 : 0.45)
                        .onTapGesture { onSelect(segment) }
                }
            }
            .padding(.horizontal, 20)
        }
    }
}

#Preview {
    ChapterTabs(
        segments: [
            BriefingSegment(id: "cold_open", label: "Good morning", startMs: 0),
            BriefingSegment(id: "weather", label: "Weather", startMs: 4000),
            BriefingSegment(id: "calendar", label: "Your day", startMs: 12000),
        ],
        activeSegmentID: "weather",
        onSelect: { _ in }
    )
    .background(Color.black)
}
