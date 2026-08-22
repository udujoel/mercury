import Foundation

// MARK: - Wire contract
//
// Reconciled against the real `POST /voice/ask` (backend/app/api/voice.py,
// backend/app/pipeline/voice.py — landed mid-session from a parallel agent;
// an earlier version of this file spoke an assumed contract designed before
// that code existed. See docs/phase3/ios_join.md for what changed.
//
// Request:  POST /voice/ask, `X-Mercury-Token` header (same as every other
//           briefing route — see `APIClient.authorize`), JSON body:
//             { "briefing_id": String, "question": String,
//               "position_ms": Int, "prior_questions": [String] }
//           (the backend also accepts an `offline` override; the client
//           deliberately never sends it, so the server's own live-vs-fake
//           credential detection applies — same convention as
//           `APIClient.requestGenerate`.)
// Response: `Content-Type: application/x-ndjson`, chunked transfer, one JSON
//           object per line — `app/api/voice.py`'s own reasoning for NDJSON
//           over a binary framing is that it is trivially parsable on both
//           ends and lets one frame carry audio and metadata together.
//           Every line has the same shape:
//             {"kind": "answer" | "bridge" | "done", "index": Int,
//              "speaker": "A" | "B", "text": String,
//              "audio_b64": String, "audio_failed": Bool,
//              "timing": {String: Int?} | null}
//           `timing` is non-null on exactly one line: the last one the
//           backend sends for this request (mirrors `VoiceChunk.timing` in
//           `app/pipeline/voice.py`, populated only on the final chunk).
//           There is no separate error frame — a mid-stream failure on the
//           backend still ends the HTTP response normally, with `kind:
//           "done"`, `audio_failed: true`, and empty audio if nothing
//           playable was ever produced. HTTP-level errors (unknown
//           briefing, empty question) come back as ordinary 404/400 status
//           codes before any streaming starts.

/// One line of the `/voice/ask` NDJSON stream, decoded.
struct VoiceAskChunk: Equatable, Sendable {
    /// Mirrors `VoiceChunk.kind` in `app/pipeline/voice.py`. `.done` is the
    /// fallback/end-of-stream marker used when nothing else was ever
    /// produced — normal streams end on an `.answer` or `.bridge` chunk
    /// that happens to also carry `isFinal == true`, not on a `.done` one.
    enum Kind: String, Equatable, Sendable {
        case answer, bridge, done
    }

    let kind: Kind
    let index: Int
    /// Which host is speaking this chunk — PRD 3.5: "the response is
    /// delivered by Host A or B, not a third assistant voice".
    let speaker: String
    let text: String
    /// Decoded audio, or `nil` if this chunk carries none (an empty
    /// `audio_b64`, or an `audio_failed: true` chunk — both mean "nothing to
    /// play here").
    let audio: Data?
    let audioFailed: Bool
    /// True for the one chunk per stream carrying the backend's own timing
    /// instrumentation — i.e. this is the last chunk `/voice/ask` will send.
    /// Mirrors `app/api/voice.py`'s own end-of-stream check
    /// (`chunk.timing is not None`).
    let isFinal: Bool

    /// Explicit memberwise init — declaring `init?(wire:)` below suppresses
    /// the synthesized one, and `JoinCoordinatorTests`' fakes need to
    /// construct scripted chunks directly without going through NDJSON.
    init(
        kind: Kind, index: Int, speaker: String, text: String,
        audio: Data?, audioFailed: Bool, isFinal: Bool
    ) {
        self.kind = kind
        self.index = index
        self.speaker = speaker
        self.text = text
        self.audio = audio
        self.audioFailed = audioFailed
        self.isFinal = isFinal
    }

    fileprivate init?(wire: WireEvent) {
        guard let kind = Kind(rawValue: wire.kind) else { return nil }
        self.kind = kind
        self.index = wire.index
        self.speaker = wire.speaker
        self.text = wire.text
        self.audio = wire.audioB64.isEmpty ? nil : Data(base64Encoded: wire.audioB64)
        self.audioFailed = wire.audioFailed
        self.isFinal = wire.timing != nil
    }
}

enum VoiceAskError: Error, LocalizedError, Equatable {
    case badStatus(Int, String)
    case malformedLine(String)
    case transport(String)

    var errorDescription: String? {
        switch self {
        case .badStatus(let code, let body): return "Voice ask returned \(code): \(body)"
        case .malformedLine(let line): return "Could not parse voice ask stream line: \(line)"
        case .transport(let message): return "Network error: \(message)"
        }
    }
}

/// Seam for `JoinCoordinator` so the "thinking → answering" orchestration —
/// react to the first chunk immediately, fold the answer text into session
/// context, surface a failure as `.answerFailed` rather than hanging — can
/// be unit tested against a scripted fake instead of a live (billable,
/// non-deterministic) backend call.
protocol VoiceAskClientProtocol {
    func ask(
        briefingId: String, question: String, positionMs: Int, priorQuestions: [String]
    ) -> AsyncThrowingStream<VoiceAskChunk, Error>
}

/// Real implementation: opens a chunked-transfer POST and decodes it as
/// NDJSON line by line via `URLSession.bytes(for:).lines`, yielding each
/// chunk the instant its line arrives rather than buffering the whole body.
final class VoiceAskClient: VoiceAskClientProtocol {

    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func ask(
        briefingId: String, question: String, positionMs: Int, priorQuestions: [String]
    ) -> AsyncThrowingStream<VoiceAskChunk, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var request = URLRequest(url: Endpoint.voiceAsk.url())
                    request.httpMethod = Endpoint.voiceAsk.method
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")
                    APIClient.authorize(&request)
                    request.httpBody = try JSONEncoder().encode(
                        RequestBody(
                            briefingId: briefingId, question: question,
                            positionMs: positionMs, priorQuestions: priorQuestions
                        )
                    )

                    let (bytes, response) = try await session.bytes(for: request)
                    if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                        throw VoiceAskError.badStatus(http.statusCode, "")
                    }

                    for try await line in bytes.lines {
                        try Task.checkCancellation()
                        guard !line.isEmpty else { continue }
                        guard let data = line.data(using: .utf8),
                              let wire = try? JSONDecoder().decode(WireEvent.self, from: data),
                              let chunk = VoiceAskChunk(wire: wire)
                        else {
                            throw VoiceAskError.malformedLine(line)
                        }
                        continuation.yield(chunk)
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch let error as VoiceAskError {
                    continuation.finish(throwing: error)
                } catch {
                    continuation.finish(throwing: VoiceAskError.transport(error.localizedDescription))
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private struct RequestBody: Encodable {
        let briefingId: String
        let question: String
        let positionMs: Int
        let priorQuestions: [String]

        enum CodingKeys: String, CodingKey {
            case briefingId = "briefing_id"
            case question
            case positionMs = "position_ms"
            case priorQuestions = "prior_questions"
        }
    }
}

/// Decodes any line of the NDJSON stream. File-scoped (not nested in
/// `VoiceAskClient`) so `VoiceAskChunk.init?(wire:)` above can see it
/// unqualified — a nested type's access level controls whether outside code
/// may reference it, not whether it can be named without qualification.
fileprivate struct WireEvent: Decodable {
    let kind: String
    let index: Int
    let speaker: String
    let text: String
    let audioB64: String
    let audioFailed: Bool
    /// `Int?` values because the backend's own type is `dict[str, int |
    /// None]` — an individual timing stage can itself be `null` (never
    /// measured), distinct from the whole field being absent (not the final
    /// chunk).
    let timing: [String: Int?]?

    enum CodingKeys: String, CodingKey {
        case kind, index, speaker, text
        case audioB64 = "audio_b64"
        case audioFailed = "audio_failed"
        case timing
    }
}
