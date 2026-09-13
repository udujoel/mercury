import XCTest
@testable import Mercury

/// What more than one OUTLET covered — task 17, answered from clusters.
///
/// `DigestStory.topics` is the obvious material and the wrong one: single
/// lowercase words, so `ai` spans 16 stories and a heading built from one
/// answers nothing. A cluster already IS a subject with its coverage
/// attached, which is the question task 17 actually asks.
final class CorroboratedStoriesTests: XCTestCase {

    private func story(
        _ headline: String, outlets: [String],
        covered: Bool = false, claimedCount: Int? = nil
    ) -> DigestStory {
        let sources = outlets.enumerated().map { i, name in
            """
            {"source_name":"\(name)","title":"\(headline) (\(i))",
             "url":"https://example.com/\(i)","source_type":"rss"}
            """
        }.joined(separator: ",")
        let json = """
        {"id":"st_\(abs(headline.hashValue))","headline":"\(headline)",
         "summary":null,"points":[],"topics":[],
         "source_count":\(claimedCount ?? outlets.count),
         "sources":[\(sources)],"covered_in_audio":\(covered)}
        """
        return try! JSONDecoder.mercury().decode(
            DigestStory.self, from: Data(json.utf8))
    }

    // MARK: - What qualifies

    func testOneOutletCoveringAStoryTwiceDoesNotQualify() {
        let items = CorroboratedStories.items(from: [
            story("Houthi advance", outlets: ["the BBC", "the BBC"]),
        ])
        XCTAssertTrue(items.isEmpty, "two articles, one outlet")
    }

    func testTwoDistinctOutletsQualify() {
        let items = CorroboratedStories.items(from: [
            story("Fake testimony", outlets: ["Ars Technica", "The Verge"]),
        ])
        XCTAssertEqual(items.first?.outletCount, 2)
    }

    /// The live bug, as a test. Every briefing already in R2 carries the
    /// inflated `source_count`, so this screen must count for itself.
    func testAnInflatedSourceCountFromAnOldManifestIsIgnored() {
        let items = CorroboratedStories.items(from: [
            story("Earn $60 for Vodafone App Feedback",
                  outlets: ["Respondent", "Respondent", "Respondent", "Respondent"],
                  claimedCount: 4),
        ])
        XCTAssertTrue(items.isEmpty,
                      "the manifest claims 4 sources; there is one outlet")
    }

    // MARK: - Ordering

    func testMostCorroboratedFirst() {
        let items = CorroboratedStories.items(from: [
            story("Two", outlets: ["A", "B"]),
            story("Three", outlets: ["A", "B", "C"]),
        ])
        XCTAssertEqual(items.map(\.story.headline), ["Three", "Two"])
    }

    func testATieBreaksTowardWhatReachedTheAudio() {
        let items = CorroboratedStories.items(from: [
            story("Unheard", outlets: ["A", "B"]),
            story("Heard", outlets: ["C", "D"], covered: true),
        ])
        XCTAssertEqual(items.first?.story.headline, "Heard")
    }

    // MARK: - Attribution

    /// `authority_sorted` is the backend's judgement about which outlet to
    /// name first, and a `Set` would scramble it — "the BBC, CNBC" reading
    /// as "CNBC, the BBC" on alternate launches looks like a bug even when
    /// nothing changed.
    func testDedupingPreservesTheBackendsOrdering() {
        let s = story("Houthi advance",
                      outlets: ["the BBC", "CNBC", "the BBC", "the New York Times"])
        XCTAssertEqual(CorroboratedStories.distinctOutlets(of: s),
                       ["the BBC", "CNBC", "the New York Times"])
    }

    func testOutletsAreNamedRatherThanCounted() {
        XCTAssertEqual(CorroboratedStories.attribution(["Ars Technica", "The Verge"]),
                       "Ars Technica and The Verge")
        XCTAssertEqual(
            CorroboratedStories.attribution(["CNBC", "the BBC", "the New York Times"]),
            "CNBC, the BBC and the New York Times")
        XCTAssertEqual(CorroboratedStories.attribution(["TLDR"]), "TLDR")
        XCTAssertEqual(CorroboratedStories.attribution([]), "")
    }

    func testAnEmptySourceNameIsNotAnOutlet() {
        let s = story("Something", outlets: ["", "CNBC"])
        XCTAssertEqual(CorroboratedStories.distinctOutlets(of: s), ["CNBC"])
    }

    // MARK: - The summary

    func testTheSummaryCountsStoriesNotOutlets() {
        let items = CorroboratedStories.items(from: [
            story("One", outlets: ["A", "B"]),
            story("Two", outlets: ["C", "D", "E"]),
        ])
        XCTAssertEqual(CorroboratedStories.summary(items),
                       "2 stories more than one outlet covered.")
    }

    func testASingleStoryReadsAsOne() {
        let items = CorroboratedStories.items(from: [story("One", outlets: ["A", "B"])])
        XCTAssertEqual(CorroboratedStories.summary(items),
                       "1 story more than one outlet covered.")
    }

    /// A quiet morning shows no section at all rather than an empty heading.
    func testNothingCorroboratedMeansNoSection() {
        XCTAssertNil(CorroboratedStories.summary([]))
        XCTAssertTrue(CorroboratedStories.items(from: [
            story("Alone", outlets: ["TLDR"]),
        ]).isEmpty)
    }
}
