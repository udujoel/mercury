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

/// Segments the listener can turn off. ROADMAP task 15.
final class DisabledSegmentsTests: XCTestCase {

    /// The wire name is what the job reads, so it is the thing to pin.
    func testItRoundTripsUnderTheNameTheJobReads() throws {
        var settings = UserSettings()
        settings.disabledSegments = ["weather", "follow_ups"]

        let data = try JSONEncoder().encode(settings)
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["disabled_segments"] as? [String],
                       ["weather", "follow_ups"])

        let back = try JSONDecoder().decode(UserSettings.self, from: data)
        XCTAssertEqual(back.disabledSegments, ["weather", "follow_ups"])
    }

    /// Absent means every segment, which is the state every install starts
    /// in and the one this must never turn into "a briefing of nothing".
    func testAbsentMeansEverythingIsOn() throws {
        let settings = try JSONDecoder().decode(
            UserSettings.self, from: Data(#"{"briefing_length":"standard"}"#.utf8))
        XCTAssertEqual(settings.disabledSegments, [])
    }

    /// The screen must not offer a switch the server would override.
    ///
    /// `STRUCTURAL_SEGMENTS` on the backend keeps `cold_open` and
    /// `sign_off` whatever the artifact says — a briefing with no greeting
    /// starts mid-sentence and one with no close just stops. Offering the
    /// toggle anyway is the same lie as a toggle nothing reads, which is
    /// the failure this whole artifact exists to prevent.
    func testTheStructuralSegmentsAreNotOffered() {
        let offered = Set(UserSettings.optionalSegments.map(\.id))
        XCTAssertFalse(offered.contains("cold_open"))
        XCTAssertFalse(offered.contains("sign_off"))
        XCTAssertEqual(
            offered,
            ["weather", "calendar", "inbox", "world", "follow_ups"],
            "these are the five `OPTIONAL_SEGMENTS` the backend resolves")
    }
}

/// Rebuilding today's briefing on demand.
final class RegenerationStatusTests: XCTestCase {

    func testItDecodesWhatTheWorkerReturns() throws {
        let status = try JSONDecoder().decode(
            RegenerationStatus.self,
            from: Data(#"{"status":"started","briefing_id":"2026-09-05","used":1,"limit":3}"#.utf8))

        XCTAssertEqual(status.briefingId, "2026-09-05")
        XCTAssertEqual(status.used, 1)
        XCTAssertEqual(status.remaining, 2)
    }

    /// The 429 body carries the same counts, so the screen can say how many
    /// are left using one shape rather than two.
    func testAtTheCapNothingIsRemaining() throws {
        let status = try JSONDecoder().decode(
            RegenerationStatus.self, from: Data(#"{"used":3,"limit":3}"#.utf8))

        XCTAssertEqual(status.remaining, 0)
    }

    /// A server that reports more used than the limit — a cap lowered
    /// between presses, say. Clamped, because "-1 rebuilds left" is not a
    /// sentence anyone should read.
    func testRemainingNeverGoesNegative() {
        XCTAssertEqual(
            RegenerationStatus(briefingId: "x", used: 9, limit: 3).remaining, 0)
    }

    /// A response with no counts is a broken response.
    ///
    /// These used to default to zero, so `{}` — or an error page, or a
    /// truncated body — decoded into a perfectly valid-looking status
    /// reading "no rebuilds left today". The button then disabled itself
    /// for the rest of the day and nothing anywhere said why.
    func testAMissingCountIsARejection() {
        for body in [#"{}"#, #"{"used":1}"#, #"{"limit":3}"#] {
            XCTAssertThrowsError(
                try JSONDecoder().decode(
                    RegenerationStatus.self, from: Data(body.utf8)),
                "decoded \(body) instead of rejecting it")
        }
    }

    /// The confirmation must not go quiet about the limit just because the
    /// server has not named a number yet — the first press of the day is
    /// the one where it matters most.
    @MainActor
    func testTheDialogAlwaysSaysTheLimitIsBounded() {
        let unknown = RegenerateButton(
            isRunning: false, remaining: nil, onConfirm: {})
        XCTAssertTrue(
            unknown.confirmationMessage.contains("few are allowed each day"),
            unknown.confirmationMessage)

        let known = RegenerateButton(
            isRunning: false, remaining: 2, onConfirm: {})
        XCTAssertTrue(
            known.confirmationMessage.contains("2 rebuilds left today"),
            known.confirmationMessage)
    }
}
