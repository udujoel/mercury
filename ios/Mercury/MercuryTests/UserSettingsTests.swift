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
            UserSettings(briefingLength: "express", disabledSegments: ["weather"],
                         ttsProvider: "elevenlabs", voiceA: "a", voiceB: "b"))

        // Snake case, matching `UserSettings` in app/core/schemas.py.
        XCTAssertEqual(json["briefing_length"] as? String, "express")
        XCTAssertEqual(json["disabled_segments"] as? [String], ["weather"])
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
         "disabled_segments": ["inbox"], "tts_provider": "elevenlabs",
         "voice_a": "v1", "voice_b": "v2", "feeds": [], "muted_senders": []}
        """.utf8))

        XCTAssertEqual(settings.briefingLength, "full")
        XCTAssertEqual(settings.disabledSegments, ["inbox"])
        XCTAssertEqual(settings.voiceA, "v1")
    }

    /// An artifact written before a field existed. Every list defaults to
    /// empty rather than failing to decode.
    func testAnOlderArtifactDecodesWithEmptyLists() throws {
        let settings = try JSONDecoder().decode(
            UserSettings.self, from: Data(#"{"briefing_length": "express"}"#.utf8))

        XCTAssertEqual(settings.briefingLength, "express")
        XCTAssertTrue(settings.disabledSegments.isEmpty)
        XCTAssertTrue(settings.feeds.isEmpty)
    }

    /// Excluded, not included: a segment added in a later release must be ON
    /// for someone who saved settings before it existed. An allow-list would
    /// hide every new segment from exactly the people who use the app most.
    func testSegmentsAreStoredAsAnExclusionList() throws {
        let settings = UserSettings(disabledSegments: ["weather"])
        let json = try encoded(settings)

        XCTAssertEqual(json["disabled_segments"] as? [String], ["weather"])
        // A segment nobody has heard of is not in the list, so it is on.
        XCTAssertFalse(settings.disabledSegments.contains("markets"))
    }

    /// The opening and the sign-off are the shape of the show, not content.
    /// A briefing that starts mid-sentence and stops dead is not shorter.
    func testTheColdOpenAndSignOffAreNotOfferedAsToggles() {
        let ids = UserSettings.optionalSegments.map(\.id)

        XCTAssertFalse(ids.contains("cold_open"))
        XCTAssertFalse(ids.contains("sign_off"))
        XCTAssertEqual(ids, ["weather", "calendar", "inbox", "world"])
    }
}
