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

/// Subjects pool separate clusters into one line.
///
/// The live digest carried "Houthi Advance Threatens Global Trade" (CNBC,
/// the BBC, the NYT) and "Houthi Advance Consolidates Red Sea Power"
/// (Al Jazeera, the BBC) as different clusters. They are the same story, and
/// listing both makes this a list of headlines again — which is what it
/// exists to stop being.
final class SubjectGroupingTests: XCTestCase {

    private func story(
        _ headline: String, subject: String?, outlets: [String], id: String
    ) -> DigestStory {
        let sources = outlets.map {
            "{\"source_name\":\"\($0)\",\"title\":\"t\",\"source_type\":\"rss\"}"
        }.joined(separator: ",")
        let subjectField = subject.map { "\"subject\":\"\($0)\"," } ?? ""
        let json = """
        {"id":"\(id)","headline":"\(headline)",\(subjectField)
         "source_count":\(outlets.count),"sources":[\(sources)]}
        """
        return try! JSONDecoder.mercury().decode(
            DigestStory.self, from: Data(json.utf8))
    }

    private let houthi = "Houthi forces advancing on Red Sea shipping lanes"

    func testTwoClustersOneSubjectBecomeOneRowWithPooledOutlets() {
        let items = CorroboratedStories.items(from: [
            story("Houthi Advance Threatens Global Trade", subject: houthi,
                  outlets: ["CNBC", "the BBC", "the New York Times"], id: "a"),
            story("Houthi Advance Consolidates Red Sea Power", subject: houthi,
                  outlets: ["Al Jazeera", "the BBC"], id: "b"),
        ])
        XCTAssertEqual(items.count, 1, "one subject, one row")
        XCTAssertEqual(items.first?.outletCount, 4,
                       "CNBC, the BBC, the NYT, Al Jazeera — the BBC once")
    }

    /// The better-corroborated telling is the one shown.
    func testTheWidestCoveredHeadlineRepresentsTheSubject() {
        let items = CorroboratedStories.items(from: [
            story("Narrow telling", subject: houthi,
                  outlets: ["Al Jazeera", "the BBC"], id: "a"),
            story("Wide telling", subject: houthi,
                  outlets: ["CNBC", "the BBC", "the New York Times"], id: "b"),
        ])
        XCTAssertEqual(items.first?.story.headline, "Wide telling")
    }

    /// Every digest written before 2026-09-13 has no subjects at all, and
    /// the archive can open any of them. They must not pool into one giant
    /// nil group.
    func testStoriesWithoutASubjectStandAlone() {
        let items = CorroboratedStories.items(from: [
            story("One", subject: nil, outlets: ["A", "B"], id: "a"),
            story("Two", subject: nil, outlets: ["C", "D"], id: "b"),
        ])
        XCTAssertEqual(items.count, 2)
    }

    func testDifferentSubjectsStayDifferentRows() {
        let items = CorroboratedStories.items(from: [
            story("Houthi", subject: houthi, outlets: ["A", "B"], id: "a"),
            story("OpenAI", subject: "OpenAI's Agents API entering public beta",
                  outlets: ["C", "D"], id: "b"),
        ])
        XCTAssertEqual(items.count, 2)
    }

    /// Pooling must not manufacture corroboration: one outlet writing twice
    /// about one subject is still one outlet.
    func testPoolingDoesNotInventOutlets() {
        let items = CorroboratedStories.items(from: [
            story("First", subject: houthi, outlets: ["the BBC"], id: "a"),
            story("Second", subject: houthi, outlets: ["the BBC"], id: "b"),
        ])
        XCTAssertTrue(items.isEmpty, "still one outlet, so still not corroborated")
    }
}
