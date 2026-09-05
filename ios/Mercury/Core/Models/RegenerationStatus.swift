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
        briefingId = try c.decodeIfPresent(String.self, forKey: .briefingId) ?? ""
        used = try c.decodeIfPresent(Int.self, forKey: .used) ?? 0
        limit = try c.decodeIfPresent(Int.self, forKey: .limit) ?? 0
    }

    init(briefingId: String, used: Int, limit: Int) {
        self.briefingId = briefingId
        self.used = used
        self.limit = limit
    }
}
