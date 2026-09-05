import XCTest
@testable import Mercury

/// The settings artifact, as the client writes and reads it.
///
/// The thing worth pinning here is the WIRE FORMAT. This object is written by
/// the phone and parsed by a job running on a different machine hours later,
/// so a key that does not match is a preference that silently does nothing —
/// which is exactly what happened with `DigestNewsletter.in_briefing`.
final class UserSettingsTests: XCTestCase {

    private func encoded(_ settings: UserSettings) throws -> [String: Any] {
        let data = try JSONEncoder().encode(settings)
        return try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testTheKeysMatchWhatTheBackendParses() throws {
        let json = try encoded(
            UserSettings(briefingLength: "express", ttsProvider: "elevenlabs",
                         voiceA: "a", voiceB: "b",
                         feeds: ["https://example.com/f"], mutedSenders: ["Substack"]))

        // Snake case, matching `UserSettings` in app/core/schemas.py.
        XCTAssertEqual(json["briefing_length"] as? String, "express")
        XCTAssertEqual(json["feeds"] as? [String], ["https://example.com/f"])
        XCTAssertEqual(json["muted_senders"] as? [String], ["Substack"])
        XCTAssertEqual(json["tts_provider"] as? String, "elevenlabs")
        XCTAssertEqual(json["voice_a"] as? String, "a")
        XCTAssertEqual(json["voice_b"] as? String, "b")
    }

    /// The job logs which settings it applied, and "when did this last
    /// change" is the first question anyone asks when a briefing comes out
    /// wrong — so both are written even though the client never reads them.
    func testTheVersionAndTimestampAreAlwaysWritten() throws {
        let json = try encoded(UserSettings())

        XCTAssertEqual(json["schema_version"] as? Int, UserSettings.schemaVersion)
        XCTAssertNotNil(json["updated_at"] as? String)
    }

    /// Null means "use the server's environment". Encoding an empty string
    /// instead would make the backend treat it as a configured value.
    func testUnsetFieldsAreOmittedRatherThanSentEmpty() throws {
        let json = try encoded(UserSettings())

        XCTAssertNil(json["briefing_length"])
        XCTAssertNil(json["voice_a"])
        XCTAssertNil(json["tts_provider"])
    }

    func testItDecodesWhatTheBackendWrites() throws {
        let settings = try JSONDecoder().decode(UserSettings.self, from: Data("""
        {"schema_version": 1, "briefing_length": "full",
         "tts_provider": "elevenlabs", "voice_a": "v1", "voice_b": "v2",
         "feeds": ["https://a"], "muted_senders": ["B"]}
        """.utf8))

        XCTAssertEqual(settings.briefingLength, "full")
        XCTAssertEqual(settings.voiceA, "v1")
        XCTAssertEqual(settings.feeds, ["https://a"])
    }

    /// An artifact written by the round that HAD segment toggles. The field
    /// is gone from the model, and an unknown key must be ignored rather
    /// than failing to decode — otherwise removing a setting would break
    /// every phone that had saved one.
    func testAnArtifactCarryingARemovedFieldStillDecodes() throws {
        let settings = try JSONDecoder().decode(UserSettings.self, from: Data("""
        {"briefing_length": "full", "disabled_segments": ["inbox"]}
        """.utf8))

        XCTAssertEqual(settings.briefingLength, "full")
    }

    /// An artifact written before a field existed. Every list defaults to
    /// empty rather than failing to decode.
    func testAnOlderArtifactDecodesWithEmptyLists() throws {
        let settings = try JSONDecoder().decode(
            UserSettings.self, from: Data(#"{"briefing_length": "express"}"#.utf8))

        XCTAssertEqual(settings.briefingLength, "express")
        XCTAssertTrue(settings.feeds.isEmpty)
        XCTAssertTrue(settings.mutedSenders.isEmpty)
    }

}

/// The voice library as the Worker reshapes it.
final class VoiceListTests: XCTestCase {

    private func decode(_ json: String) throws -> VoiceList {
        try JSONDecoder().decode(VoiceList.self, from: Data(json.utf8))
    }

    func testItDecodesTheShapeTheWorkerReturns() throws {
        let list = try decode("""
        {"voices": [
            {"voice_id": "nPczCjzI2devNBz1zQrb", "name": "Brian",
             "preview_url": "https://cdn.example.com/brian.mp3",
             "labels": {"accent": "american", "age": "middle-aged",
                        "use_case": "narration"}}
        ]}
        """)

        let voice = try XCTUnwrap(list.voices.first)
        XCTAssertEqual(voice.name, "Brian")
        XCTAssertEqual(voice.previewURL?.lastPathComponent, "brian.mp3")
        // Fixed order, so two voices are comparable at a glance rather than
        // dictionary-ordered differently on each launch.
        XCTAssertEqual(voice.traits, "american · middle-aged · narration")
    }

    /// Some cloned voices have no sample. The row has to render without the
    /// play button rather than assuming one is there.
    func testAVoiceWithNoSampleStillDecodes() throws {
        let list = try decode("""
        {"voices": [{"voice_id": "v1", "name": "Cloned", "preview_url": null}]}
        """)
        XCTAssertNil(list.voices.first?.previewURL)
    }

    /// A deployment with no ElevenLabs key. Not an error — the picker says
    /// so and offers manual entry, rather than showing an empty list that
    /// reads as "there are no voices".
    func testAnUnconfiguredDeploymentCarriesAReasonRatherThanJustNoVoices() throws {
        let list = try decode(#"{"voices": [], "detail": "ElevenLabs is not configured."}"#)

        XCTAssertTrue(list.voices.isEmpty)
        XCTAssertEqual(list.detail, "ElevenLabs is not configured.")
    }

    func testAVoiceWithNoLabelsHasNoTraitsRatherThanStrayDots() throws {
        let list = try decode(#"{"voices": [{"voice_id": "v1", "name": "N"}]}"#)
        XCTAssertEqual(list.voices.first?.traits, "")
    }

    /// The music set round-trips under the key the backend parses.
    func testTheMusicSetUsesTheBackendsKey() throws {
        let data = try JSONEncoder().encode(UserSettings(musicSet: "broadsheet"))
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["music_set"] as? String, "broadsheet")
    }

    /// "none" is a real choice, not an absence: someone who wants silence
    /// should be able to say so rather than have it mean "use the default".
    func testSilenceIsAChoiceInItsOwnRight() {
        XCTAssertTrue(UserSettings.musicSets.contains { $0.id == "none" })
        XCTAssertEqual(UserSettings.musicSets.map(\.id).prefix(3).sorted(),
                       ["broadsheet", "first-light", "paper"])
    }
}
