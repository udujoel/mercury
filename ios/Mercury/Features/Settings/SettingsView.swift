import SwiftUI

/// Settings, and the one distinction that shapes the whole screen.
///
/// Some of these change the DEVICE and some change the JOB. Wake time and
/// appearance affect the phone, so `UserDefaults` is the right home — the
/// 04:00 cron does not care when your phone rings. Length, voices, feeds and
/// segments affect the briefing that gets MADE, on a machine that cannot read
/// your phone's defaults, so those are saved to an artifact the job reads at
/// the top of every run.
///
/// The screen says which is which, because "saved" meaning two different
/// things is exactly how a settings page becomes decoration.
struct SettingsView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var settings = UserSettings()
    @State private var loaded = false
    @State private var saveState: SaveState = .idle

    enum SaveState: Equatable {
        case idle
        case saving
        case saved
        case failed(String)
    }

    var body: some View {
        @Bindable var state = appState

        Form {
            Section {
                DatePicker(
                    "Wake time",
                    selection: Binding(
                        get: { appState.wakeTimeDate },
                        set: { appState.wakeTimeDate = $0 }
                    ),
                    displayedComponents: .hourAndMinute
                )
            } header: {
                Text("On this phone")
            } footer: {
                Text("When Mercury wakes you. Stored on the device — the "
                     + "briefing is made overnight either way.")
            }

            Section {
                Picker("Length", selection: lengthBinding) {
                    ForEach(BriefingLength.allCases) { length in
                        Text(length.label).tag(length.rawValue)
                    }
                }

                ForEach(UserSettings.optionalSegments, id: \.id) { segment in
                    Toggle(segment.label, isOn: segmentBinding(segment.id))
                }
            } header: {
                Text("The briefing")
            } footer: {
                // Naming the cold open and sign-off explicitly, because their
                // absence from this list is a decision rather than an
                // oversight: a briefing that starts mid-sentence and stops
                // dead is not a shorter briefing.
                Text("Which parts the hosts cover. The opening and the "
                     + "sign-off are always there. Takes effect on the next "
                     + "briefing, not this morning's.")
            }

            Section {
                Picker("Voice", selection: providerBinding) {
                    Text("Google").tag("google")
                    Text("ElevenLabs").tag("elevenlabs")
                }
                if settings.ttsProvider == "elevenlabs" {
                    LabeledContent("Host A") {
                        TextField("Voice ID", text: voiceBinding(\.voiceA))
                            .multilineTextAlignment(.trailing)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                    LabeledContent("Host B") {
                        TextField("Voice ID", text: voiceBinding(\.voiceB))
                            .multilineTextAlignment(.trailing)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                }
            } header: {
                Text("Voice")
            } footer: {
                // Honest about the gap rather than presenting a text field as
                // if it were a picker. Listing real voices needs the
                // ElevenLabs key, and the key belongs on the server — putting
                // it on the phone to populate a menu would be the wrong trade
                // by a wide margin.
                Text(settings.ttsProvider == "elevenlabs"
                     ? "Voice IDs from elevenlabs.io — Voices, then Copy Voice "
                       + "ID. A picker needs the API key, which stays on the "
                       + "server."
                     : "Google is the fallback whenever ElevenLabs is "
                       + "unavailable, so a briefing always ships.")
            }

            Section {
                Link("Manage access", destination: Self.googlePermissions)
                Link("Privacy policy", destination: Self.privacyPolicy)
            } header: {
                Text("Account and data")
            } footer: {
                Text("Mercury reads Gmail and Calendar with read-only "
                     + "permissions and cannot send, delete or change "
                     + "anything. Raw email is never stored — only the "
                     + "script, the audio and where each item came from.")
            }

            Section {
                save
            } footer: {
                footerText
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            guard !loaded else { return }
            loaded = true
            if let saved = try? await appState.fetchSavedSettings() {
                settings = saved
            }
        }
    }

    // MARK: - Saving

    @ViewBuilder
    private var save: some View {
        Button {
            Task { await commit() }
        } label: {
            HStack {
                Text(saveState == .saving ? "Saving…" : "Save for the next briefing")
                Spacer(minLength: 0)
                if saveState == .saving { ProgressView() }
                if saveState == .saved {
                    Image(systemName: "checkmark")
                        .foregroundStyle(MercuryColor.accent.color)
                }
            }
        }
        .disabled(saveState == .saving)
    }

    @ViewBuilder
    private var footerText: some View {
        if case .failed(let message) = saveState {
            Text("Not saved. \(message)")
                .foregroundStyle(.red)
        } else {
            Text("These are read by the job that makes your briefing "
                 + "overnight, so they only apply from the next one on.")
        }
    }

    private func commit() async {
        saveState = .saving
        do {
            try await appState.saveSettings(settings)
            saveState = .saved
        } catch {
            saveState = .failed(error.localizedDescription)
        }
    }

    // MARK: - Bindings

    /// Writes through to `settings` AND clears the saved flag.
    ///
    /// A tick still showing next to "Saved" while the user changes something
    /// else is the screen claiming the new value is stored when it is not.
    private func edit(_ change: (inout UserSettings) -> Void) {
        change(&settings)
        if saveState == .saved { saveState = .idle }
    }

    private var lengthBinding: Binding<String> {
        Binding(
            get: { settings.briefingLength ?? BriefingLength.standard.rawValue },
            set: { value in edit { $0.briefingLength = value } }
        )
    }

    private var providerBinding: Binding<String> {
        Binding(
            get: { settings.ttsProvider ?? "google" },
            set: { value in edit { $0.ttsProvider = value } }
        )
    }

    /// Inverted: the model stores what is OFF, the toggle shows what is ON.
    private func segmentBinding(_ id: String) -> Binding<Bool> {
        Binding(
            get: { !settings.disabledSegments.contains(id) },
            set: { isOn in
                edit {
                    if isOn {
                        $0.disabledSegments.removeAll { $0 == id }
                    } else if !$0.disabledSegments.contains(id) {
                        $0.disabledSegments.append(id)
                    }
                }
            }
        )
    }

    /// Empty text and "unset" are the same thing here: a blank field must
    /// clear the value rather than save an empty string the server would
    /// then treat as a configured voice id.
    private func voiceBinding(_ key: WritableKeyPath<UserSettings, String?>) -> Binding<String> {
        Binding(
            get: { settings[keyPath: key] ?? "" },
            set: { value in
                let trimmed = value.trimmingCharacters(in: .whitespaces)
                edit { $0[keyPath: key] = trimmed.isEmpty ? nil : trimmed }
            }
        )
    }

    private static let googlePermissions =
        URL(string: "https://myaccount.google.com/permissions")!
    private static let privacyPolicy =
        URL(string: "https://joeludu.com/mercury/privacy")!
}
