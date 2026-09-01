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
                        // The active pill is a FILL with text drawn on it, so
                        // it takes `accent`/`inkOnAccent` rather than an ink
                        // token. Renaming it to `ink` alongside the other
                        // textPrimary call sites would have made it near-black
                        // on near-black — the deprecation alias preserves the
                        // name, not the role, and this is one of the two places
                        // where the role is the thing that matters.
                        .foregroundStyle(
                            isActive ? MercuryColor.inkOnAccent.color : MercuryColor.inkMuted.color
                        )
                        .padding(.horizontal, 16)
                        .padding(.vertical, 14)   // 44pt minimum touch target
                        .background(
                            Capsule().fill(
                                isActive
                                    ? MercuryColor.accent.color
                                    : MercuryColor.hairline.color
                            )
                        )
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
