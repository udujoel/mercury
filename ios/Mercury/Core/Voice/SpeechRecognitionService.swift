import Foundation
import Speech
import AVFoundation

/// What `SpeechRecognizing.requestAuthorization()` can come back with.
/// Mirrors the states `SFSpeechRecognizer` and `AVAudioSession`/
/// `AVAudioApplication` actually expose, collapsed to what the Join flow
/// needs to branch on.
enum SpeechAuthStatus: Equatable, Sendable {
    case authorized
    /// Either speech recognition or the microphone was denied/restricted.
    /// The flow must not sit in `.listening` forever waiting for a result
    /// that authorization will never allow — see `JoinCoordinator`.
    case denied
    case notDetermined
}

/// One transcript update from a listening session.
enum SpeechRecognitionEvent: Equatable, Sendable {
    /// Interim text — endpointing has not fired yet.
    case partial(String)
    /// Input amplitude just dropped below the speech threshold right after
    /// having been above it — i.e. the user (probably) just stopped
    /// talking. Fires zero or more times per session, and may be superseded
    /// by a later one if the user resumes and pauses again; only the most
    /// recent one before `.final` is the real "speech end" instant.
    ///
    /// This exists specifically so `JoinCoordinator` can measure PRD 3.5's
    /// "speech end → detected < 600ms" budget line as the gap it actually
    /// means: the lag between the user physically stopping and
    /// `SFSpeechRecognizer`'s endpointing deciding so (`.final`) — not the
    /// (always ~0ms) gap between two client-side events that fire together.
    case silenceStarted
    /// Endpointing decided the user is done talking. Terminal for the
    /// session; `String` is final text (may be empty if nothing was heard).
    case final(String)
}

/// Seam between `JoinCoordinator` and Apple's speech APIs. Exists so the
/// coordinator's orchestration (which state comes next, when the timing
/// recorder gets marked, how a denial is handled) can be unit tested against
/// a fake — real `SFSpeechRecognizer` needs a microphone and a physical
/// device, neither of which exist in CI or the simulator.
protocol SpeechRecognizing: AnyObject {
    func requestAuthorization() async -> SpeechAuthStatus

    /// Starts on-device recognition. `onEvent` fires for every partial and
    /// exactly once with `.final` before the session ends on its own.
    /// Throws synchronously if the audio session/engine could not start.
    func startListening(onEvent: @escaping (SpeechRecognitionEvent) -> Void) throws

    /// Ends the session early — used for the tap-again and timeout exits out
    /// of `.listening`. Safe to call even if nothing is in progress.
    func stopListening()
}

/// `SFSpeechRecognizer`-backed implementation.
///
/// `requiresOnDeviceRecognition = true` per PRD 3.5: no network round trip,
/// no audio leaving the device for transcription, and it's free. The PRD
/// allows falling back to a network recognizer (or Whisper) on low
/// confidence; that fallback is NOT implemented here — see the report's
/// "what needs a device" list. On-device recognition cannot be exercised at
/// all in the iOS Simulator (there is no on-device model bundled there), so
/// this whole class is necessarily unverified by the test suite; only its
/// conformance to `SpeechRecognizing` (i.e. that it compiles against the
/// protocol `JoinCoordinator` depends on) is checked by the build.
final class SFSpeechRecognitionService: NSObject, SpeechRecognizing {

    /// Natural pauses (breath, "um... actually") should not cut the user off
    /// mid-question. `SFSpeechRecognizer`'s automatic endpointing is tuned
    /// for dictation, which is snappier than we want here; forcing a longer
    /// trailing-silence requirement via `addsPunctuation`/the task hint
    /// alone isn't controllable, so this class also runs its own
    /// silence-timeout as a backstop (see `silenceTimeout`) in case the
    /// system recognizer's own endpointing doesn't fire.
    private let silenceTimeout: TimeInterval

    private let recognizer: SFSpeechRecognizer?
    private let audioEngine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var silenceTimer: Timer?
    private var lastPartialAt: Date?
    private var lastPartialText: String = ""

    /// RMS amplitude threshold below which a buffer counts as silence.
    /// Float32 samples in [-1, 1]; 0.02 sits comfortably below normal speech
    /// and above typical mic self-noise/room tone.
    private static let speechRMSThreshold: Float = 0.02
    private var hasDetectedSpeech = false
    private var silenceStartFired = false

    init(locale: Locale = Locale(identifier: "en-US"), silenceTimeout: TimeInterval = 1.1) {
        self.recognizer = SFSpeechRecognizer(locale: locale)
        self.silenceTimeout = silenceTimeout
        super.init()
    }

    func requestAuthorization() async -> SpeechAuthStatus {
        let speechStatus = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
        guard speechStatus == .authorized else {
            return speechStatus == .notDetermined ? .notDetermined : .denied
        }

        let micGranted = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            if #available(iOS 17.0, *) {
                AVAudioApplication.requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            } else {
                AVAudioSession.sharedInstance().requestRecordPermission { granted in
                    continuation.resume(returning: granted)
                }
            }
        }
        return micGranted ? .authorized : .denied
    }

    func startListening(onEvent: @escaping (SpeechRecognitionEvent) -> Void) throws {
        stopListening()

        guard let recognizer, recognizer.isAvailable else {
            throw SpeechRecognitionError.recognizerUnavailable
        }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .measurement, options: [.duckOthers, .allowBluetoothHFP])
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        // Free, private, no network round trip — PRD 3.5 is explicit this is
        // the primary path.
        request.requiresOnDeviceRecognition = true
        self.request = request

        hasDetectedSpeech = false
        silenceStartFired = false

        let input = audioEngine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            self?.request?.append(buffer)
            self?.detectSilenceEdge(in: buffer, onEvent: onEvent)
        }

        audioEngine.prepare()
        try audioEngine.start()

        lastPartialAt = Date()
        lastPartialText = ""
        armSilenceTimer(onEvent: onEvent)

        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let text = result.bestTranscription.formattedString
                self.lastPartialAt = Date()
                self.lastPartialText = text
                if result.isFinal {
                    onEvent(.final(text))
                    self.stopListening()
                } else {
                    onEvent(.partial(text))
                }
            }
            if error != nil {
                // A cancellation from `stopListening()` also lands here as
                // an error — that's expected, not a failure to surface.
                self.stopListening()
            }
        }
    }

    func stopListening() {
        silenceTimer?.invalidate()
        silenceTimer = nil
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()
        request = nil
        task?.cancel()
        task = nil
    }

    /// Computes the RMS of one input buffer and reports the speaking→silence
    /// edge (see `SpeechRecognitionEvent.silenceStarted`). Runs on the audio
    /// engine's real-time render thread via the tap callback — kept to a
    /// single pass over the buffer with no allocation so it can't cause an
    /// audio dropout.
    private func detectSilenceEdge(in buffer: AVAudioPCMBuffer, onEvent: @escaping (SpeechRecognitionEvent) -> Void) {
        guard let channelData = buffer.floatChannelData else { return }
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0 else { return }

        var sumSquares: Float = 0
        let samples = channelData[0]
        for i in 0..<frameCount {
            let sample = samples[i]
            sumSquares += sample * sample
        }
        let rms = (sumSquares / Float(frameCount)).squareRoot()

        if rms >= Self.speechRMSThreshold {
            hasDetectedSpeech = true
            silenceStartFired = false
        } else if hasDetectedSpeech, !silenceStartFired {
            silenceStartFired = true
            onEvent(.silenceStarted)
        }
    }

    /// Backstop endpointing: if no new partial has arrived for
    /// `silenceTimeout`, treat whatever the last partial said as final. Runs
    /// alongside `SFSpeechRecognitionTask`'s own `isFinal` signal — whichever
    /// fires first wins, since `stopListening()` cancels the other path.
    private func armSilenceTimer(onEvent: @escaping (SpeechRecognitionEvent) -> Void) {
        silenceTimer?.invalidate()
        silenceTimer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] timer in
            guard let self, let lastPartialAt = self.lastPartialAt else { return }
            if Date().timeIntervalSince(lastPartialAt) >= self.silenceTimeout {
                timer.invalidate()
                onEvent(.final(self.lastPartialText))
                self.stopListening()
            }
        }
    }
}

enum SpeechRecognitionError: Error, LocalizedError {
    case recognizerUnavailable

    var errorDescription: String? {
        switch self {
        case .recognizerUnavailable:
            return "On-device speech recognition is not available for this locale/device."
        }
    }
}
