import Foundation

/// The arithmetic behind the transcript, kept out of the view.
///
/// Same reason `PlayerTransport` exists: a `View` cannot be asserted on, and
/// every one of these has an off-by-one waiting in it — which index is
/// speaking, how far through the segments you are, what a turn with no timing
/// should do.
enum TranscriptScript {

    /// Where a line sits relative to the one being spoken.
    ///
    /// Three states rather than two. A single highlight on an otherwise
    /// uniform field reads as a slideshow; it is the contrast between what
    /// you have heard and what is coming that makes a script feel followed
    /// rather than displayed.
    enum LineState {
        case spoken
        case speaking
        case unspoken
    }

    /// `isFinished` is not a detail. `PlaybackCoordinator.finish()` keeps
    /// `currentTurn` set — deliberately, so the transcript holds the last
    /// line rather than blanking — so without it the final line stays lit as
    /// speaking after the audio has stopped, and VoiceOver goes on
    /// announcing "Now playing" over silence.
    static func state(ofTurnAt index: Int, activeIndex: Int?,
                      isFinished: Bool = false) -> LineState {
        // The briefing has been heard, all of it. Nothing is speaking and
        // nothing is still ahead of you.
        if isFinished { return .spoken }
        guard let activeIndex else { return .unspoken }
        if index == activeIndex { return .speaking }
        return index < activeIndex ? .spoken : .unspoken
    }

    /// The index of the turn being spoken, by identity rather than by time.
    ///
    /// The coordinator has already done the time lookup — including the
    /// awkward part, where a position lands in the silence between two turns
    /// and has to resolve forward. Re-deriving it here from `positionMs`
    /// would be a second implementation of that rule, free to disagree with
    /// the first.
    static func activeIndex(of turn: Turn?, in turns: [Turn]) -> Int? {
        guard let turn else { return nil }
        return turns.firstIndex(of: turn)
    }

    /// Whether a speaker label is drawn above this line.
    ///
    /// Only when the speaker changes. A name over every line turns a
    /// conversation into a list of attributions, and the hosts alternate
    /// often enough that most lines would carry one.
    static func showsSpeaker(at index: Int, in turns: [Turn]) -> Bool {
        guard turns.indices.contains(index) else { return false }
        guard index > 0 else { return true }
        return turns[index - 1].speaker != turns[index].speaker
    }

    /// "3 of 7" for the segment currently playing.
    ///
    /// One-based, because it is read aloud by VoiceOver and shown to a
    /// person. Nil when the segment is not in the list — a manifest whose
    /// turns name a segment its own `segments` array omits, which the
    /// backend should not produce and which must not become a crash if it
    /// ever does.
    static func position(ofSegment id: String?, in segments: [BriefingSegment]) -> (index: Int, total: Int)? {
        guard let id, let i = segments.firstIndex(where: { $0.id == id }) else { return nil }
        return (i + 1, segments.count)
    }

    /// "Your world · 3 of 7", or just the label when the position is unknown.
    static func segmentCaption(label: String?, position: (index: Int, total: Int)?) -> String? {
        guard let label else { return nil }
        guard let position else { return label }
        return "\(label) · \(position.index) of \(position.total)"
    }
}
