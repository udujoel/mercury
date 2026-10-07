import XCTest
@testable import Mercury

/// The outlet marks and the words beside them.
///
/// Every case here is one a simpler rule gets wrong on a real morning: the
/// first-letter-of-each-word rule makes "The New York Times" TN, a
/// first-letter-only rule makes the BBC a lone "B", and counting `sources`
/// rather than outlets makes one newsletter that wrote twice look like two
/// publications agreeing.
final class SourceStackTests: XCTestCase {

    // MARK: - Monograms

    func testArticlesNeverEarnALetter() {
        XCTAssertEqual(SourceAttribution.monogram(for: "The New York Times"), "NY")
        XCTAssertEqual(SourceAttribution.monogram(for: "The Verge"), "V")
        XCTAssertEqual(SourceAttribution.monogram(for: "the BBC"), "BBC")
    }

    func testTwoSignificantWordsGiveTwoInitials() {
        XCTAssertEqual(SourceAttribution.monogram(for: "Financial Times"), "FT")
        XCTAssertEqual(SourceAttribution.monogram(for: "Hacker News"), "HN")
        XCTAssertEqual(SourceAttribution.monogram(for: "Morning Brew Daily"), "MB")
    }

    func testAnAcronymKeepsItsLettersUpToThree() {
        XCTAssertEqual(SourceAttribution.monogram(for: "NPR"), "NPR")
        XCTAssertEqual(SourceAttribution.monogram(for: "CNBC"), "CNB")
        XCTAssertEqual(SourceAttribution.monogram(for: "TLDR AI"), "TLD")
    }

    func testANumberIsNotAnAcronym() {
        XCTAssertEqual(SourceAttribution.monogram(for: "1440"), "1")
        XCTAssertEqual(SourceAttribution.monogram(for: "1440 Daily Digest"), "1D")
    }

    func testASingleLowercaseWordIsUppercased() {
        XCTAssertEqual(SourceAttribution.monogram(for: "stratechery"), "S")
    }

    func testPunctuationIsNotALetter() {
        // "Ars Technica" spelled with a separator the split must see.
        XCTAssertEqual(SourceAttribution.monogram(for: "Ars-Technica"), "AT")
        XCTAssertEqual(SourceAttribution.monogram(for: "  Reuters  "), "R")
    }

    func testANameWithNoLettersStillGetsAMark() {
        // A blank disc reads as an image that failed to load.
        XCTAssertEqual(SourceAttribution.monogram(for: ""), "•")
        XCTAssertEqual(SourceAttribution.monogram(for: "—"), "•")
        XCTAssertEqual(SourceAttribution.monogram(for: "The"), "•")
    }

    // MARK: - Labels

    func testCompactNamesTheFirstAndCountsTheRest() {
        XCTAssertEqual(SourceAttribution.compact([]), "")
        XCTAssertEqual(SourceAttribution.compact(["Reuters"]), "Reuters")
        XCTAssertEqual(SourceAttribution.compact(["Reuters", "the BBC", "NPR"]), "Reuters +2")
    }

    func testBylineNamesUpToThreeInFull() {
        XCTAssertEqual(SourceAttribution.byline(["Reuters"]), "Reuters")
        XCTAssertEqual(SourceAttribution.byline(["Reuters", "the BBC"]),
                       "Reuters and the BBC")
        XCTAssertEqual(SourceAttribution.byline(["Reuters", "the BBC", "NPR"]),
                       "Reuters, the BBC and NPR")
    }

    func testBylinePastThreeNamesTwoAndCountsTheRest() {
        XCTAssertEqual(
            SourceAttribution.byline(["Reuters", "the BBC", "NPR", "CNBC", "Axios"]),
            "Reuters, the BBC and 3 more")
    }

    func testABylineOfExactlyFourDoesNotSayOneMore() {
        // "A, B and 2 more" — never "A, B, C and 1 more", which spends more
        // words than naming the fourth would.
        XCTAssertEqual(
            SourceAttribution.byline(["Reuters", "the BBC", "NPR", "CNBC"]),
            "Reuters, the BBC and 2 more")
    }

    // MARK: - Outlets, not articles

    func testOneOutletWritingTwiceIsOneOutlet() {
        let story = DigestStory(
            id: "s", headline: "h",
            sources: [
                DigestSource(sourceName: "TLDR", title: "one"),
                DigestSource(sourceName: "TLDR", title: "two"),
                DigestSource(sourceName: "the BBC", title: "three"),
            ])
        XCTAssertEqual(SourceAttribution.outlets(of: story), ["TLDR", "the BBC"])
        XCTAssertEqual(SourceAttribution.compact(SourceAttribution.outlets(of: story)),
                       "TLDR +1", "two publications, not three")
    }

    func testOutletOrderIsTheBackendsAuthorityOrder() {
        let story = DigestStory(
            id: "s", headline: "h",
            sources: ["Reuters", "Axios", "the BBC"].map {
                DigestSource(sourceName: $0, title: $0)
            })
        XCTAssertEqual(SourceAttribution.outlets(of: story), ["Reuters", "Axios", "the BBC"])
    }

    // MARK: - The coverage header

    func testCoverageCountsOutletsWhenEachWroteOnce() {
        XCTAssertEqual(StoryDetailView.coverageLabel(articles: 1, outlets: 1),
                       "Coverage · 1 outlet")
        XCTAssertEqual(StoryDetailView.coverageLabel(articles: 3, outlets: 3),
                       "Coverage · 3 outlets")
    }

    func testCoverageSaysBothNumbersWhenTheyDiffer() {
        // The rows are articles and the byline counts outlets; a header
        // naming only one would contradict something else on the screen.
        XCTAssertEqual(StoryDetailView.coverageLabel(articles: 4, outlets: 3),
                       "Coverage · 4 articles from 3 outlets")
        XCTAssertEqual(StoryDetailView.coverageLabel(articles: 2, outlets: 1),
                       "Coverage · 2 articles from 1 outlet")
    }

    func testCoverageWithNoNamedOutletStillCountsTheArticles() {
        XCTAssertEqual(StoryDetailView.coverageLabel(articles: 2, outlets: 0),
                       "Coverage · 2 articles")
    }
}
