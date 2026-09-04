import SwiftUI

/// The transcript as a script you can follow, not a single line in a void.
///
/// What this replaces: one turn at a time, cross-fading. That is a caption
/// track. It showed the sentence being spoken and nothing either side of it,
/// so there was no way to see where the conversation had been or where it was
/// going, no way to skim, and no way to jump to a line you could see.
///
/// PRD 3.4's argument survives ADR-012 intact: the text *is* the visual
/// composition. A script is more type, not less — the state of every line is
/// carried by ink weight and colour rather than by a highlight bar, which is
/// what keeps it reading as a printed page rather than a karaoke screen.
///
/// **Three states, not two.** Spoken is faintest, speaking is full ink,
/// unspoken sits between them. A single highlight on an otherwise uniform
/// field reads as a slideshow; the contrast between what you have heard and
/// what is coming is what makes it feel followed.
///
/// Word-level highlighting inside the speaking line is task 6 in
/// `docs/ROADMAP.md`, and it needs word timings that no manifest has yet.
/// Everything here works on `start_ms` per turn, which every manifest already
/// carries — which is why it ships first.
struct TranscriptView: View {
    @Environment(\.mercuryMotion) private var motion

    let turns: [Turn]
    let activeTurn: Turn?
    /// True once the audio has run out. The coordinator keeps `currentTurn`
    /// set after finishing so the last line stays on screen; without this the
    /// script would go on claiming to be speaking it.
    var isFinished: Bool = false
    /// False until there is audio in the engine, matching the scrubber and
    /// the chapter ribbon: tapping a line is a seek, and a seek with no file
    /// does nothing.
    var isEnabled: Bool = true
    let onSelect: (Turn) -> Void

    private var activeIndex: Int? {
        TranscriptScript.activeIndex(of: activeTurn, in: turns)
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: MercurySpace.comfortable) {
                    ForEach(Array(turns.enumerated()), id: \.element.id) { index, turn in
                        line(turn, at: index)
                            .id(turn.id)
                    }
                }
                .mercuryGutter()
                // Half a screen of air at each end, so the first and last
                // lines can still reach the reading position rather than
                // stopping at the edge of the scroll view.
                .padding(.vertical, MercurySpace.section)
            }
            .onChange(of: activeTurn?.id) { _, id in
                guard let id else { return }
                withAnimation(motion.resolve(MercuryMotion.crossfade)) {
                    proxy.scrollTo(id, anchor: Self.readingPosition)
                }
            }
            // Entering mid-briefing has to start in the right place, and the
            // scroll view has not measured its content when `onAppear` runs
            // — the same yield ChapterTabs documents.
            .task {
                await Task.yield()
                guard let id = activeTurn?.id else { return }
                proxy.scrollTo(id, anchor: Self.readingPosition)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Slightly above the middle, not at it.
    ///
    /// Reading runs downward, so the eye wants the next line already visible.
    /// Anchoring at dead centre leaves half the screen showing text that has
    /// been read and pushes what is coming to the very bottom.
    private static let readingPosition = UnitPoint(x: 0, y: 0.38)

    @ViewBuilder
    private func line(_ turn: Turn, at index: Int) -> some View {
        let state = TranscriptScript.state(
            ofTurnAt: index, activeIndex: activeIndex, isFinished: isFinished)

        VStack(alignment: .leading, spacing: MercurySpace.tight) {
            if TranscriptScript.showsSpeaker(at: index, in: turns) {
                Text(turn.speaker)
                    .font(MercuryType.eyebrow)
                    .textCase(.uppercase)
                    .tracking(0.6)
                    .foregroundStyle(speakerColor(turn.speaker).color)
                    .accessibilityLabel("Host \(turn.speaker)")
            }

            Text(turn.text)
                .font(MercuryType.transcript)
                // Ragged right, per ADR-012.
                .multilineTextAlignment(.leading)
                .lineSpacing(MercuryType.voiceLineSpacing)
                .foregroundStyle(color(for: state).color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .contentShape(Rectangle())
        .onTapGesture { onSelect(turn) }
        .allowsHitTesting(isEnabled)
        .animation(motion.resolve(MercuryMotion.crossfade), value: state)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel("\(turn.speaker): \(turn.text)")
        .accessibilityValue(state == .speaking ? "Now playing" : "")
        .accessibilityHint(isEnabled ? "Plays from this line" : "")
    }

    private func color(for state: TranscriptScript.LineState) -> AdaptiveColor {
        switch state {
        case .speaking: return MercuryColor.ink
        case .unspoken: return MercuryColor.inkMuted
        case .spoken: return MercuryColor.inkFaint
        }
    }

    /// The two hosts, told apart.
    ///
    /// The day's accent marks the second speaker, which is the only place in
    /// the transcript colour appears and the only thing in it colour could
    /// honestly mean. Deliberately keyed off the first speaker seen rather
    /// than a hardcoded "A"/"B": the backend names hosts in the script and
    /// those names are not a fixed pair.
    private func speakerColor(_ speaker: String) -> AdaptiveColor {
        speaker == turns.first?.speaker ? MercuryColor.ink : MercuryColor.accent
    }
}

// MARK: - Where you are

/// The segment being discussed, centred beneath the script.
///
/// It reads "Your world · 3 of 7". The chapter ribbon says where you can go;
/// this says where you are, in words, at the place the eye leaves the text —
/// which is what makes the shape of the briefing legible while listening
/// rather than only when scrubbing.
struct SegmentIndicator: View {
    let label: String?
    let position: (index: Int, total: Int)?

    var body: some View {
        if let caption = TranscriptScript.segmentCaption(label: label, position: position) {
            Text(caption)
                .font(MercuryType.eyebrow)
                .textCase(.uppercase)
                .tracking(0.6)
                .foregroundStyle(MercuryColor.inkFaint.color)
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(
                    position.map { "\(label ?? "") , part \($0.index) of \($0.total)" } ?? (label ?? "")
                )
        }
    }
}

#Preview("Script") {
    let turns = [
        Turn(speaker: "Maya", segment: "world",
             text: "…and the acquisition closed on Tuesday.", startMs: 0, endMs: 4_000),
        Turn(speaker: "Alex", segment: "world",
             text: "Northwind paid four point two billion for a company nobody had heard of.",
             startMs: 4_000, endMs: 11_000),
        Turn(speaker: "Maya", segment: "world",
             text: "That is a lot for a robotics startup with no revenue at all.",
             startMs: 11_000, endMs: 16_000),
        Turn(speaker: "Alex", segment: "world",
             text: "The regulators have already signalled they will look at it.",
             startMs: 16_000, endMs: 21_000),
    ]
    return VStack(spacing: 0) {
        TranscriptView(turns: turns, activeTurn: turns[1], onSelect: { _ in })
        SegmentIndicator(label: "Your world", position: (3, 7))
            .padding(.bottom, MercurySpace.comfortable)
    }
    .background(MercuryColor.surface.color)
}
