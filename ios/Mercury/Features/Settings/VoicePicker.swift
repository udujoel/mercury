import AVFoundation
import SwiftUI

/// One voice from the ElevenLabs library, as the Worker reshapes it.
///
/// Reshaped rather than proxied: the upstream response carries settings,
/// sharing metadata and fine-tuning state a picker has no use for and that
/// would cost a phone bytes for nothing.
struct VoiceOption: Decodable, Identifiable, Equatable {
    let voiceID: String
    let name: String
    /// The sample this picker plays. Absent for some cloned voices, so a row
    /// has to render without one rather than assuming it is there.
    let previewURL: URL?
    let description: String?
    let labels: [String: String]

    var id: String { voiceID }

    /// "British · calm · narration" — whatever the library actually says,
    /// in a fixed order so two voices are comparable at a glance.
    var traits: String {
        ["accent", "age", "gender", "use_case", "description"]
            .compactMap { labels[$0] }
            .joined(separator: " · ")
    }

    enum CodingKeys: String, CodingKey {
        case name, description, labels
        case voiceID = "voice_id"
        case previewURL = "preview_url"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        voiceID = try c.decode(String.self, forKey: .voiceID)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? voiceID
        previewURL = (try c.decodeIfPresent(String.self, forKey: .previewURL))
            .flatMap(URL.init(string:))
        description = try c.decodeIfPresent(String.self, forKey: .description)
        labels = try c.decodeIfPresent([String: String].self, forKey: .labels) ?? [:]
    }

    init(voiceID: String, name: String, previewURL: URL? = nil,
         description: String? = nil, labels: [String: String] = [:]) {
        self.voiceID = voiceID
        self.name = name
        self.previewURL = previewURL
        self.description = description
        self.labels = labels
    }
}

struct VoiceList: Decodable {
    let voices: [VoiceOption]
    /// Present when the list could not be built — no key configured, or
    /// ElevenLabs unreachable. The picker says so rather than showing an
    /// empty list, which reads as "there are no voices".
    let detail: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        voices = try c.decodeIfPresent([VoiceOption].self, forKey: .voices) ?? []
        detail = try c.decodeIfPresent(String.self, forKey: .detail)
    }

    enum CodingKeys: String, CodingKey { case voices, detail }

    init(voices: [VoiceOption], detail: String? = nil) {
        self.voices = voices
        self.detail = detail
    }

    /// What a deployment with no ElevenLabs key gets. Not an error — the
    /// picker says so and the id can still be typed.
    static let empty = VoiceList(
        voices: [], detail: "ElevenLabs is not configured.")
}

/// Pick a host voice, and hear it first.
///
/// The point of this screen is the sample. A voice id is an opaque string,
/// and choosing a host by reading `nPczCjzI2devNBz1zQrb` is not choosing.
///
/// The list comes from the Worker, not from ElevenLabs directly — listing
/// voices needs the API key, and a key on a phone is a key in a binary
/// anyone can pull apart. The Worker already holds it and spends it on one
/// read-only GET.
struct VoicePicker: View {
    let title: String
    @Binding var selection: String?

    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var state: LoadState = .loading
    @State private var previewing: String?
    @State private var player: AVPlayer?

    enum LoadState: Equatable {
        case loading
        case ready([VoiceOption])
        /// Includes "ElevenLabs is not configured", which is not an error
        /// so much as a different state of the world — the footer says so
        /// and the id can still be typed on the previous screen.
        case unavailable(String)
    }

    var body: some View {
        Group {
            switch state {
            case .loading:
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            case .unavailable(let message):
                unavailable(message)
            case .ready(let voices):
                List(voices) { voice in
                    row(voice)
                }
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .onDisappear { stopPreview() }
    }

    private func row(_ voice: VoiceOption) -> some View {
        HStack(spacing: MercurySpace.snug) {
            Button {
                selection = voice.voiceID
                stopPreview()
                dismiss()
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: MercurySpace.tight) {
                        Text(voice.name)
                            .font(MercuryType.cardBody)
                            .foregroundStyle(MercuryColor.ink.color)
                        if voice.voiceID == selection {
                            Image(systemName: "checkmark")
                                .font(.footnote)
                                .foregroundStyle(MercuryColor.accent.color)
                        }
                    }
                    if !voice.traits.isEmpty {
                        Text(voice.traits)
                            .font(MercuryType.figureCaption)
                            .foregroundStyle(MercuryColor.inkFaint.color)
                            .lineLimit(2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            // Separate from the row's own tap: hearing a voice and choosing
            // it are different intentions, and merging them means you cannot
            // audition without committing.
            if let url = voice.previewURL {
                Button { preview(voice.voiceID, url) } label: {
                    Image(systemName: previewing == voice.voiceID
                          ? "stop.circle" : "play.circle")
                        .font(.title3)
                        .foregroundStyle(MercuryColor.accent.color)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(
                    previewing == voice.voiceID
                        ? "Stop sample" : "Play a sample of \(voice.name)")
            }
        }
        .accessibilityElement(children: .contain)
    }

    /// No list — and a way through anyway.
    ///
    /// The text field is here rather than on the settings screen because
    /// this is the only place it is needed: when the library loads, nobody
    /// wants to type an opaque id. An earlier cut of this told the user to
    /// "paste a voice ID on the previous screen" after the picker had
    /// replaced those very fields, which was instructions to do something
    /// that was no longer possible.
    private func unavailable(_ message: String) -> some View {
        Form {
            Section {
                TextField("Voice ID", text: manualEntry)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } header: {
                Text("Enter an ID")
            } footer: {
                Text("From elevenlabs.io — Voices, then Copy Voice ID.")
            }

            Section {
                Text(message)
                    .font(MercuryType.cardBody)
                    .foregroundStyle(MercuryColor.inkMuted.color)
                    .fixedSize(horizontal: false, vertical: true)
            } header: {
                Text("Why there is no list")
            }
        }
    }

    /// Empty clears the selection rather than saving an empty string the
    /// server would read as a configured voice.
    private var manualEntry: Binding<String> {
        Binding(
            get: { selection ?? "" },
            set: { value in
                let trimmed = value.trimmingCharacters(in: .whitespaces)
                selection = trimmed.isEmpty ? nil : trimmed
            }
        )
    }

    private func load() async {
        guard state == .loading else { return }
        do {
            let list = try await appState.fetchVoices()
            if let detail = list.detail, list.voices.isEmpty {
                state = .unavailable(detail)
            } else {
                state = .ready(list.voices)
            }
        } catch {
            state = .unavailable(error.localizedDescription)
        }
    }

    // MARK: - Sampling

    /// Plays through `AVPlayer` on its own, deliberately NOT through
    /// `PlaybackCoordinator`.
    ///
    /// The coordinator owns the briefing: a loaded file, a position, resume
    /// persistence and the now-playing info the lock screen reads. Auditioning
    /// a three-second sample through it would overwrite all of that, and the
    /// user would come back from Settings to find their place lost.
    private func preview(_ id: String, _ url: URL) {
        if previewing == id {
            stopPreview()
            return
        }
        stopPreview()
        let player = AVPlayer(url: url)
        self.player = player
        previewing = id
        player.play()
    }

    private func stopPreview() {
        player?.pause()
        player = nil
        previewing = nil
    }
}

#Preview("Voices") {
    NavigationStack {
        VoicePicker(title: "Host A", selection: .constant("nPczCjzI2devNBz1zQrb"))
    }
}
