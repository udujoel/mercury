import AVFoundation

/// Plays the streamed answer (and the bridge line after it) as a strict FIFO
/// of short clips — one `AVAudioPlayer` per chunk, chained by delegate
/// callback so the next chunk starts the instant the previous one ends.
///
/// This is deliberately a second, much simpler audio path than
/// `PlaybackCoordinator`'s `AVAudioEngine` graph. ADR-005 justifies
/// `AVAudioEngine` for the *briefing* because the transcript view needs
/// sample-accurate scrub position; the answer clip has no transcript to sync
/// and is never seeked or scrubbed, so `AVAudioPlayer` — far less code — is
/// the right tool here rather than routing it through the same engine.
///
/// Chunks may arrive from the network faster than they play (buffering
/// ahead) or slower (the player sits idle waiting for the next `enqueue`) —
/// both are handled: `enqueue` while a chunk is already playing just appends,
/// and the delegate callback pulls the next one only once one exists.
protocol AnswerPlaying: AnyObject {
    /// Fires once, the moment the very first chunk actually starts playing —
    /// this is the "first token → audio playing" budget line's end mark.
    var onFirstChunkStarted: (() -> Void)? { get set }
    /// Fires once every enqueued chunk (answer + bridge) has finished
    /// playing AND `finishEnqueuing()` has been called — i.e. there is
    /// nothing left coming and nothing left queued.
    var onFinished: (() -> Void)? { get set }

    func enqueue(audio: Data)
    /// Signals no more chunks are coming. Safe to call before or after the
    /// last chunk finishes playing — `onFinished` fires whichever comes
    /// later.
    func finishEnqueuing()
    /// Stops playback immediately and clears the queue. Used by the tap-
    /// again cancel path if it ever needs to interrupt an in-progress
    /// answer (not currently reachable — `.answering` has no `.tapMic` edge
    /// in `JoinStateMachine` — but kept as a hard reset for error paths).
    func stopAll()
}

final class AnswerAudioPlayer: NSObject, AnswerPlaying, AVAudioPlayerDelegate {

    var onFirstChunkStarted: (() -> Void)?
    var onFinished: (() -> Void)?

    private var pending: [Data] = []
    private var current: AVAudioPlayer?
    private var didStartFirst = false
    private var doneEnqueuing = false

    func enqueue(audio: Data) {
        pending.append(audio)
        playNextIfIdle()
    }

    func finishEnqueuing() {
        doneEnqueuing = true
        if current == nil && pending.isEmpty {
            onFinished?()
        }
    }

    func stopAll() {
        current?.stop()
        current = nil
        pending.removeAll()
        doneEnqueuing = false
        didStartFirst = false
    }

    private func playNextIfIdle() {
        guard current == nil, !pending.isEmpty else { return }
        let next = pending.removeFirst()
        do {
            let player = try AVAudioPlayer(data: next)
            player.delegate = self
            current = player
            player.play()
            if !didStartFirst {
                didStartFirst = true
                onFirstChunkStarted?()
            }
        } catch {
            // A single malformed chunk shouldn't wedge the rest of the
            // answer — skip it and try the next one (graceful degradation,
            // CLAUDE.md rule 7).
            playNextIfIdle()
        }
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        current = nil
        if !pending.isEmpty {
            playNextIfIdle()
        } else if doneEnqueuing {
            onFinished?()
        }
    }
}
