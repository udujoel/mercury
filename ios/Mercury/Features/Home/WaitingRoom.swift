import AVFoundation
import SwiftUI

/// The exception path, given a sound.
///
/// **When this appears, and only then.** ADR-001 means the server generates
/// overnight and the audio is finished before you open the app, so there is
/// no dead air to fill on a normal morning — the roadmap is explicit that
/// this is "the exception path only: opening before the cron ran, or after a
/// failed run". Every other state, including a briefing that exists but
/// fails to download, gets the ordinary error. A waiting room that showed up
/// on a good morning would be a bug, and `WaitingRoom.shouldEnter` is a pure
/// function precisely so a test can pin that down.
///
/// **Why it is entirely on-device.** It exists for the case where the server
/// has nothing to give, so it cannot depend on the server giving it
/// something. The bed is bundled and the line is spoken by
/// `AVSpeechSynthesizer` — no network, no ElevenLabs spend, and it still
/// works on a plane. The system voice is not Mara, and that is the honest
/// trade: on an exception path a real voice saying a true thing beats
/// silence, and beats a spinner.
enum WaitingRoom {

    /// The one sentence the backend sends when the day's job has not run.
    ///
    /// Matched on the STATUS rather than the prose. `AppState` builds this
    /// 404 itself when the read-only Worker answers 405, so the text is
    /// ours and could be reworded by anyone; the status is the contract.
    static func shouldEnter(status: Int?) -> Bool { status == 404 }

    /// What the holding line says.
    ///
    /// Grounded, like everything else the hosts say: it states when the
    /// briefing is made and what to do, and promises nothing about when it
    /// will appear. "Any moment now" would be a guess, and the run it is
    /// waiting for may have failed.
    static let line =
        "Your briefing isn't ready yet. It's put together early each morning, "
        + "so there should be one waiting for you shortly."
}

/// Plays the bed and speaks the line, then stops.
///
/// Deliberately NOT routed through `PlaybackCoordinator`. That object owns
/// the briefing — a loaded file, a position, resume persistence and the
/// now-playing card the lock screen reads — and borrowing it for a fifteen
/// second holding loop would overwrite all of it, so the user would come
/// back from a failed morning to find their place in yesterday's briefing
/// lost. The same reason `VoicePicker` samples on its own player.
@Observable
@MainActor
final class WaitingRoomPlayer {

    private(set) var isPlaying = false

    private var bed: AVAudioPlayer?
    private let speaker = AVSpeechSynthesizer()

    /// How quiet the bed sits under the voice.
    ///
    /// Lower than the stitcher's -8 dB, because there the music is mixed
    /// against a mastered voice track and here it is competing with the
    /// system synthesiser at whatever volume the phone is set to.
    private static let bedVolume: Float = 0.18

    func start() {
        guard !isPlaying else { return }
        isPlaying = true

        // Ambient, NOT playback. This is the one piece of audio in Mercury
        // that should obey the Ring/Silent switch: a briefing is something
        // you asked to hear, and this is the app talking back on a morning
        // it has nothing for you. Waking a silenced phone to say "nothing
        // yet" is exactly the behaviour that gets an app deleted.
        try? AVAudioSession.sharedInstance().setCategory(.ambient, mode: .spokenAudio)
        try? AVAudioSession.sharedInstance().setActive(true)

        if let url = Bundle.main.url(forResource: "waiting-bed", withExtension: "mp3"),
           let player = try? AVAudioPlayer(contentsOf: url) {
            player.numberOfLoops = -1
            player.volume = Self.bedVolume
            player.play()
            bed = player
        }

        let utterance = AVSpeechUtterance(string: WaitingRoom.line)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.95
        // A beat of bed before the voice, so it opens rather than barges in.
        utterance.preUtteranceDelay = 1.2
        speaker.speak(utterance)
    }

    func stop() {
        // `.immediate`, because this is called from `onDisappear`: a
        // `.word` boundary would keep talking over whatever screen the user
        // moved to.
        speaker.stopSpeaking(at: .immediate)
        bed?.stop()
        bed = nil
        isPlaying = false
        try? AVAudioSession.sharedInstance().setActive(
            false, options: .notifyOthersOnDeactivation)
    }
}

/// The card itself: what is happening, and a way to try again.
struct WaitingRoomCard: View {
    let onRetry: () -> Void

    @State private var player = WaitingRoomPlayer()

    var body: some View {
        VStack(alignment: .leading, spacing: MercurySpace.comfortable) {
            HStack(spacing: MercurySpace.tight) {
                Image(systemName: "waveform")
                    .font(.caption)
                Text("Nothing yet")
                    .font(MercuryType.eyebrow)
                    .textCase(.uppercase)
                    .tracking(0.6)
                Spacer(minLength: 0)
            }
            .foregroundStyle(MercuryColor.inkFaint.color)

            Text("Your briefing isn't ready")
                .font(MercuryType.greeting)
                .foregroundStyle(MercuryColor.ink.color)
                .fixedSize(horizontal: false, vertical: true)

            Text("It's put together early each morning. If you're up before "
                 + "it, or last night's run didn't finish, this is what you "
                 + "get — and there should be one waiting for you shortly.")
                .font(MercuryType.cardBody)
                .foregroundStyle(MercuryColor.inkMuted.color)
                .fixedSize(horizontal: false, vertical: true)

            Button(action: onRetry) {
                Text("Try again")
                    .font(MercuryType.cardTitle)
                    .foregroundStyle(MercuryColor.inkOnAccent.color)
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: 50)
                    .background(MercuryColor.accent.color, in: Capsule())
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(MercurySpace.comfortable)
        .mercuryCard(.raised)
        .onAppear { player.start() }
        .onDisappear { player.stop() }
        .accessibilityElement(children: .contain)
    }
}

#Preview("Waiting room") {
    WaitingRoomCard(onRetry: {})
        .mercuryGutter()
        .background(MercuryColor.surface.color)
}
