import SwiftUI

/// The chapter ribbon. Active chapter is a filled pill in the day's accent;
/// tapping one seeks to that chapter's start.
///
/// **It now follows playback.** It did not: the ribbon scrolled only when a
/// finger moved it, so from the fourth chapter onward the active pill was off
/// the right-hand edge with nothing on screen saying so. A ribbon that shows
/// where you are is the entire reason it is a ribbon rather than a list, and
/// it was the one thing it did not do.
struct ChapterTabs: View {
    @Environment(\.mercuryMotion) private var motion
    /// Not decoration. Changing the text size re-lays the pills out at new
    /// widths and the scroll view keeps its old *offset*, which now points
    /// somewhere else entirely — observed on the simulator: at
    /// `accessibility1` the ribbon jumped from the active Sign-off pill back
    /// to the middle of the list and stayed there until the next segment
    /// change. Re-centring on the size is what makes "follows playback" true
    /// at every text size rather than only at the default one.
    @Environment(\.dynamicTypeSize) private var typeSize

    let segments: [BriefingSegment]
    let activeSegmentID: String?
    let onSelect: (BriefingSegment) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: MercurySpace.snug) {
                    ForEach(segments) { segment in
                        pill(segment)
                            .id(segment.id)
                    }
                }
                .padding(.horizontal, MercurySpace.gutter)
                .padding(.vertical, MercurySpace.tight)
            }
            .onChange(of: activeSegmentID) { _, id in
                scroll(proxy, to: id)
            }
            .onChange(of: typeSize) { _, _ in
                scroll(proxy, to: activeSegmentID)
            }
            // Entering the player mid-briefing has to start in the right
            // place too — otherwise the ribbon is only correct for people
            // who listened from the beginning without leaving.
            //
            // `task` rather than `onAppear`: `onAppear` runs before the
            // ScrollView has measured its content, and a `scrollTo` issued
            // then is silently dropped. Yielding once puts it after the
            // first layout pass.
            .task {
                await Task.yield()
                scroll(proxy, to: activeSegmentID)
            }
        }
    }

    private func scroll(_ proxy: ScrollViewProxy, to id: String?) {
        guard let id else { return }
        // `.center` rather than `.leading`: a chapter is easier to place when
        // you can see the ones on either side of it.
        withAnimation(motion.resolve(MercuryMotion.reposition)) {
            proxy.scrollTo(id, anchor: .center)
        }
    }

    private func pill(_ segment: BriefingSegment) -> some View {
        let isActive = segment.id == activeSegmentID
        return Button {
            onSelect(segment)
        } label: {
            Text(segment.label)
                .font(MercuryType.chapterTab)
                .lineLimit(1)
                // The active pill is a FILL with text drawn on it, so it
                // takes `accent`/`inkOnAccent` rather than an ink token.
                // Renaming it to `ink` alongside the other textPrimary call
                // sites would have made it near-black on near-black.
                .foregroundStyle(isActive
                                 ? MercuryColor.inkOnAccent.color
                                 : MercuryColor.inkMuted.color)
                .padding(.horizontal, MercurySpace.comfortable)
                .frame(minHeight: 44)
                .background(
                    Capsule().fill(isActive
                                   ? MercuryColor.accent.color
                                   : MercuryColor.hairline.color)
                )
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(segment.label)
        .accessibilityHint("Jumps to this chapter")
        .accessibilityAddTraits(isActive ? [.isButton, .isSelected] : .isButton)
    }
}

#Preview {
    ChapterTabs(
        segments: [
            BriefingSegment(id: "cold_open", label: "Good morning", startMs: 0),
            BriefingSegment(id: "weather", label: "Weather", startMs: 4000),
            BriefingSegment(id: "calendar", label: "Your day", startMs: 12000),
            BriefingSegment(id: "inbox", label: "Your inbox", startMs: 30000),
            BriefingSegment(id: "world", label: "Your world", startMs: 60000),
            BriefingSegment(id: "sign_off", label: "Sign-off", startMs: 90000),
        ],
        activeSegmentID: "world",
        onSelect: { _ in }
    )
    .background(MercuryColor.surface.color)
}
