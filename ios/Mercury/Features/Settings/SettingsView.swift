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

    @State private var settings = UserSettings()
    @State private var loadState: LoadState = .loading
    @State private var edited = false
    @State private var saveState: SaveState = .idle

    enum LoadState: Equatable {
        case loading
        /// Loaded, or confirmed never-saved. Both are safe to edit from.
        case ready
        /// The request failed. Saving from here would PUT this screen's
        /// defaults over settings the server still holds — silently
        /// replacing preferences with the ones a blank form happens to
        /// contain. So editing and saving are both blocked.
        case failed(String)
    }

    enum SaveState: Equatable {
        case idle
        case saving
        case saved
        case failed(String)
    }

    private var isEditable: Bool { loadState == .ready && saveState != .saving }

    var body: some View {
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
                    Text("Server default").tag(Self.unsetTag)
                    ForEach(BriefingLength.allCases) { length in
                        Text(length.label).tag(length.rawValue)
                    }
                }

            } header: {
                Text("The briefing")
            } footer: {
                // No segment toggles. They were here for one round and are
                // deliberately gone: the script chain requires every
                // canonical segment to be present, so a toggle would have
                // stored a preference nothing reads. ROADMAP task 15.
                Text("Takes effect on the next briefing, not this morning's.")
            }
            .disabled(!isEditable)

            Section {
                Picker("Voice", selection: providerBinding) {
                    Text("Server default").tag(Self.unsetTag)
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
                Text(voiceNote)
            }
            .disabled(!isEditable)

            Section {
                sources
            } header: {
                Text("Sources")
            }
            .disabled(!isEditable)

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
        // NOT form-wide. Wake time is stored on this device and does not
        // come from the artifact, so disabling it because a SERVER read
        // failed would take away the one setting that still works. Each
        // server-backed section carries the flag instead.
        .task {
            guard loadState == .loading, !edited else { return }
            do {
                let saved = try await appState.fetchSavedSettings()
                // The load has RESOLVED either way — that is what `.ready`
                // means, and it must be set before the edit check. Returning
                // early left `loadState == .loading` forever, so `isEditable`
                // stayed false and the form was permanently disabled for
                // anyone who typed while it was loading. A worse bug than
                // the one being fixed.
                loadState = .ready
                // The VALUES are only adopted when nothing has been typed. A
                // slow response landing after an edit would overwrite it and
                // then save the old values.
                guard !edited else { return }
                settings = saved ?? UserSettings()
            } catch {
                loadState = .failed(error.localizedDescription)
            }
        }
    }

    // MARK: - Sources

    /// Factored out of `body` because the type-checker gave up on it inline.
    @ViewBuilder
    private var sources: some View {
        LineListEditor(
            title: "News feeds",
            placeholder: "One feed URL per line",
            lines: linesBinding(\.feeds),
            emptyNote: Self.feedsNote
        )
        LineListEditor(
            title: "Muted senders",
            placeholder: "One sender per line",
            lines: linesBinding(\.mutedSenders),
            emptyNote: Self.mutedNote
        )
    }

    private static let feedsNote = "Empty means Mercury's curated feeds."

    /// Honest about the gap rather than presenting a text field as if it
    /// were a picker: listing real voices needs the ElevenLabs key, and the
    /// key belongs on the server.
    private var voiceNote: String {
        if settings.ttsProvider == "elevenlabs" {
            return "Voice IDs from elevenlabs.io \u{2014} Voices, then Copy "
                + "Voice ID. A picker needs the API key, which stays on the "
                + "server."
        }
        return "Google is the fallback whenever ElevenLabs is unavailable, "
            + "so a briefing always ships."
    }

    /// Says what muting does AND what it does not. Muted mail still reaches
    /// the desk — suppressing it from both would leave no way to tell a
    /// muted publication from one that never arrived.
    private static let mutedNote =
        "Matched loosely, so \"Substack\" mutes every sender whose name "
        + "contains it. Muted mail still appears on the desk \u{2014} it is "
        + "just not read aloud."

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
        // Enabled by the section, but stated here too: with a failed load
        // this button would PUT a blank form over real settings.
        .disabled(!isEditable)
    }

    @ViewBuilder
    private var footerText: some View {
        if case .failed(let message) = loadState {
            // Named as a LOAD failure, not a save failure. "Could not save"
            // over a screen that never loaded would send the user looking in
            // the wrong place.
            Text("Could not load your settings, so they cannot be changed "
                 + "from here yet — saving now would replace them with this "
                 + "screen's defaults. \(message)")
                .foregroundStyle(.red)
        } else if case .failed(let message) = saveState {
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
            // The artifact is read by the overnight job; `briefingLength` in
            // UserDefaults is what an ON-DEMAND generate uses. Without this
            // they disagree the moment the length is changed here, and
            // tapping the card would generate at the old length while every
            // scheduled run used the new one.
            if let raw = settings.briefingLength,
               let length = BriefingLength(rawValue: raw) {
                appState.briefingLength = length
            }
            saveState = .saved
            edited = false
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
        edited = true
        if saveState == .saved { saveState = .idle }
    }

    /// The tag meaning "no value in the artifact — use whatever the server
    /// is configured with".
    ///
    /// Both pickers used to fall back to a concrete value when unset, which
    /// was wrong twice over: the screen SHOWED "Standard" and "Google" for a
    /// deployment configured otherwise, and saving anything at all then
    /// wrote those defaults over the deployment's own configuration. Someone
    /// who opened settings to mute a sender would have silently pinned the
    /// length and switched the voice back to Google.
    private static let unsetTag = ""

    private var lengthBinding: Binding<String> {
        Binding(
            get: { settings.briefingLength ?? Self.unsetTag },
            set: { value in
                edit { $0.briefingLength = value.isEmpty ? nil : value }
            }
        )
    }

    private var providerBinding: Binding<String> {
        Binding(
            get: { settings.ttsProvider ?? Self.unsetTag },
            set: { value in
                edit {
                    if value.isEmpty {
                        // "Server default" means the environment decides ALL
                        // of it. Leaving the voice ids behind sent them
                        // anyway, so the provider deferred to the server
                        // while the voices still overrode it — a state no
                        // control on this screen was asking for.
                        $0.ttsProvider = nil
                        $0.voiceA = nil
                        $0.voiceB = nil
                    } else {
                        $0.ttsProvider = value
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

    /// A `[String]` edited as lines of text.
    ///
    /// Blank lines are dropped on the way in, so trailing newlines while
    /// typing do not become empty feed URLs the backend would then try to
    /// fetch.
    private func linesBinding(
        _ key: WritableKeyPath<UserSettings, [String]>
    ) -> Binding<String> {
        Binding(
            get: { settings[keyPath: key].joined(separator: "\n") },
            set: { text in
                let lines = text
                    .split(separator: "\n", omittingEmptySubsequences: true)
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                    .filter { !$0.isEmpty }
                edit { $0[keyPath: key] = lines }
            }
        )
    }

    private static let googlePermissions =
        URL(string: "https://myaccount.google.com/permissions")!
    private static let privacyPolicy =
        URL(string: "https://joeludu.com/mercury/privacy")!
}

/// A list of strings, edited as lines.
///
/// A `TextEditor` rather than an add/remove list: these are pasted far more
/// often than they are typed one at a time — a feed URL comes off a
/// clipboard, and a set of senders is easier to review as a block than as
/// eight rows with delete buttons.
struct LineListEditor: View {
    let title: String
    let placeholder: String
    @Binding var lines: String
    let emptyNote: String

    var body: some View {
        VStack(alignment: .leading, spacing: MercurySpace.tight) {
            Text(title)
                .font(MercuryType.cardBody)
                .foregroundStyle(MercuryColor.ink.color)

            ZStack(alignment: .topLeading) {
                if lines.isEmpty {
                    // `TextEditor` has no placeholder of its own, and an
                    // empty box gives no clue what belongs in it.
                    Text(placeholder)
                        .font(MercuryType.cardBody)
                        .foregroundStyle(MercuryColor.inkFaint.color)
                        .padding(.top, 8)
                        .padding(.leading, 5)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $lines)
                    .font(MercuryType.cardBody)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .frame(minHeight: 88)
                    .scrollContentBackground(.hidden)
            }

            Text(emptyNote)
                .font(MercuryType.figureCaption)
                .foregroundStyle(MercuryColor.inkFaint.color)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, MercurySpace.tight)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(title)
    }
}
