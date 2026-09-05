import Foundation

/// What the server says after being asked to rebuild today's briefing.
///
/// The counts are the point. This is the one action in Mercury that costs
/// real money per press — a full Gemini and ElevenLabs generation — so the
/// screen has to be able to say "two of three left" rather than letting
/// someone discover the limit by hitting it.
struct RegenerationStatus: Decodable, Equatable {
    let briefingId: String
    let used: Int
    let limit: Int

    var remaining: Int { max(limit - used, 0) }

    enum CodingKeys: String, CodingKey {
        case used, limit
        case briefingId = "briefing_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Optional, and only this one. The 429 and 409 bodies carry the
        // counts but no id, because refusing a regeneration does not name a
        // briefing.
        briefingId = try c.decodeIfPresent(String.self, forKey: .briefingId) ?? ""
        // REQUIRED. These defaulted to 0, which decoded any object at all —
        // `{}` included — into a status reading "no rebuilds left today",
        // silently disabling the button on a malformed response instead of
        // surfacing it as the decoding failure it is. Every shape the Worker
        // returns for this route carries both.
        used = try c.decode(Int.self, forKey: .used)
        limit = try c.decode(Int.self, forKey: .limit)
    }

    init(briefingId: String, used: Int, limit: Int) {
        self.briefingId = briefingId
        self.used = used
        self.limit = limit
    }
}
