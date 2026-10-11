import SafariServices
import SwiftUI

/// One story, opened — laid out the way Particle lays out a story.
///
/// The reading half of Mercury, and the thing the digest was built to feed.
/// Particle's story page has a shape worth copying because every part of it
/// answers a question in the order a reader asks them: what is this (the
/// picture and the headline), who says so (the outlets, directly under the
/// headline rather than at the foot of the page), what happened (the points),
/// why should I care, and where can I read more (big rows that open the
/// publisher). Mercury adds one thing Particle puts behind a tab: when the
/// hosts discussed the story, a way into the audio at that exact turn.
///
/// Bullets rather than the paragraph wherever both exist. `points` is written
/// as its own output for this screen; `summary` is the fallback for a digest
/// generated before bullets existed, and for a story whose enrichment failed
/// there is neither, which is a headline and its sources and that is honest.
struct StoryDetailView: View {
    let story: DigestStory
    /// Nil when the hosts did not discuss this story, which is most of the
    /// desk on a busy morning. The button is absent rather than disabled —
    /// there is no audio to enable it for.
    let onPlay: ((Int) -> Void)?

    @State private var openURL: URL?

    /// Distinct outlets, worked out once. A computed property rebuilt the
    /// list on every read, and `body` reads it more than once.
    private let outlets: [String]

    init(story: DigestStory, onPlay: ((Int) -> Void)?) {
        self.story = story
        self.onPlay = onPlay
        self.outlets = SourceAttribution.outlets(of: story)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                hero

                VStack(alignment: .leading, spacing: MercurySpace.comfortable) {
                    eyebrow
                    headline
                    if !outlets.isEmpty {
                        SourceByline(outlets: outlets)
                            .foregroundStyle(MercuryColor.inkMuted.color)
                    }
                    if let onPlay, let index = story.turnIndex { listen(onPlay, index) }
                    summary
                    if let why = story.whyItMatters { whyItMatters(why) }
                    coverage
                }
                .mercuryGutter()
                .padding(.top, MercurySpace.generous)
                .padding(.bottom, MercurySpace.section)
            }
        }
        .background(MercuryColor.surface.color.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .hardTopScrollEdge()
        .sheet(item: $openURL) { url in
            // In-app, not a jump to Safari. The morning ritual is the app;
            // being thrown out of it to read one article and having to find
            // your way back is how a briefing becomes a browsing session.
            SafariView(url: url)
                .ignoresSafeArea()
        }
    }

    // MARK: - What is this

    /// The lead image, edge to edge, or the sunrise where one would be.
    ///
    /// Full bleed, as Particle does it: inset inside the gutter with rounded
    /// corners it read as a thumbnail that had been enlarged, which is a
    /// card's treatment rather than a page's.
    ///
    /// The fallback is `MercuryMark` — the same mark `DeskFeed` draws for a
    /// story with no picture — and not a pale accent band. A newsletter-only
    /// story HAS no picture, because rule 5 discards the body its images live
    /// in, so this is the ordinary case on a quiet morning; if the row shows
    /// the sunrise and the page shows a tinted strip, opening the story looks
    /// like something failed to load. Shorter than a photograph, because a
    /// mark is not something to look at for long.
    private var hero: some View {
        // `Color.clear` decides the size and the artwork is an OVERLAY, so
        // the picture can never widen the page. A fill-mode image overflows
        // the size it is offered by design, and as a direct child it widened
        // its frame and pushed the text with it — the exact defect
        // `LeadCard`'s comment records.
        Color.clear
            .frame(maxWidth: .infinity)
            .frame(height: story.imageURL == nil ? 140 : 240)
            .overlay { artwork }
            .clipped()
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(MercuryColor.hairline.color)
                    .frame(height: 1)
            }
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var artwork: some View {
        if let url = story.imageURL {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().aspectRatio(contentMode: .fill)
                default:
                    MercuryMark()
                }
            }
        } else {
            MercuryMark()
        }
    }

    /// "IN YOUR BRIEFING · 2 HOURS AGO".
    ///
    /// The waveform and the accent only when the hosts discussed it, which is
    /// the one fact about this story that no other app could tell you.
    @ViewBuilder
    private var eyebrow: some View {
        let age = Self.age(of: story)
        if story.coveredInAudio || age != nil {
            HStack(spacing: MercurySpace.tight) {
                if story.coveredInAudio {
                    Image(systemName: "waveform")
                        .font(.caption2)
                    Text("In your briefing")
                    if age != nil { Text("·") }
                }
                if let age { Text(age).foregroundStyle(MercuryColor.inkFaint.color) }
            }
            .font(MercuryType.eyebrow)
            .textCase(.uppercase)
            .tracking(0.6)
            .foregroundStyle(MercuryColor.accent.color)
            .accessibilityElement(children: .combine)
        }
    }

    private var headline: some View {
        Text(story.headline)
            .font(MercuryType.headline)
            .foregroundStyle(MercuryColor.ink.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityAddTraits(.isHeader)
    }

    /// Newest article's age, relative. Shared with the desk's eyebrow so a
    /// story cannot be "2 hours ago" on the row and something else here.
    static func age(of story: DigestStory) -> String? {
        guard let published = story.sources.compactMap(\.publishedAt).max()
        else { return nil }
        return published.formatted(.relative(presentation: .numeric))
    }

    // MARK: - Hear it

    /// A pill, near the top, rather than a text link after the summary.
    ///
    /// It sat between "why it matters" and the sources, styled as a line of
    /// accent text — below the fold on most stories, and indistinguishable
    /// from a heading. The audio is the one thing on this page Particle
    /// cannot offer, so it goes where Particle puts its own listen control:
    /// under the byline, as a control that looks like one.
    private func listen(_ onPlay: @escaping (Int) -> Void, _ index: Int) -> some View {
        Button { onPlay(index) } label: {
            HStack(spacing: MercurySpace.snug) {
                Image(systemName: "play.fill")
                    .font(.footnote)
                Text("Hear the hosts on this")
                    .font(MercuryType.chapterTab)
            }
            .foregroundStyle(MercuryColor.inkOnAccent.color)
            .padding(.horizontal, MercurySpace.comfortable)
            .frame(minHeight: 44)
            .background(Capsule().fill(MercuryColor.accent.color))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityHint("Plays the briefing from where this story is discussed")
    }

    // MARK: - What happened

    /// The points, on a card of their own.
    ///
    /// A raised card rather than bullets loose on the page: on Particle the
    /// summary is the object, and everything else on the screen is about it.
    @ViewBuilder
    private var summary: some View {
        if !story.points.isEmpty {
            VStack(alignment: .leading, spacing: MercurySpace.base) {
                ForEach(Array(story.points.enumerated()), id: \.offset) { _, point in
                    HStack(alignment: .firstTextBaseline, spacing: MercurySpace.base) {
                        // Drawn here, not carried in the string — the backend
                        // strips any marker the model added, so this is the
                        // only place a bullet exists.
                        Circle()
                            .fill(MercuryColor.accent.color)
                            .frame(width: 6, height: 6)
                            // Sits on the first line's x-height rather than
                            // its baseline, where a bare dot reads as a full
                            // stop that wandered.
                            .alignmentGuide(.firstTextBaseline) { d in d.height + 4 }
                            .accessibilityHidden(true)
                        Text(point)
                            .font(MercuryType.reading)
                            .foregroundStyle(MercuryColor.ink.color)
                            .lineSpacing(MercuryType.interfaceLineSpacing)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(MercurySpace.comfortable)
            .mercuryCard(.raised)
        } else if let paragraph = story.summary {
            Text(paragraph)
                .font(MercuryType.reading)
                .foregroundStyle(MercuryColor.ink.color)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Why care

    /// A pull-out with a rule down its left edge, the way a printed page sets
    /// an aside. Ink rather than muted ink: this is the sentence the reader
    /// came for, not a footnote to it.
    private func whyItMatters(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: MercurySpace.tight) {
            SectionLabel("Why it matters")
            Text(text)
                .font(MercuryType.reading)
                .foregroundStyle(MercuryColor.ink.color)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.leading, MercurySpace.base + 3)
        // An overlay, not an HStack sibling: an overlay is offered the
        // text's own height, so the rule runs the full length of the aside.
        // As a sibling under `fixedSize` it was offered no height at all and
        // drew at a shape's 10pt ideal — a tick, not a rule.
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(MercuryColor.accent.color)
                .frame(width: 3)
                .accessibilityHidden(true)
        }
        .padding(.vertical, MercurySpace.tight)
    }

    // MARK: - Read more

    @ViewBuilder
    private var coverage: some View {
        // Counted from what is actually RENDERED, not from `source_count`.
        // A legacy story with a count of 3 and an empty array said
        // "3 sources" above nothing at all, which implies coverage exists
        // and is being withheld. The section disappears instead.
        if !story.sources.isEmpty {
            VStack(alignment: .leading, spacing: MercurySpace.snug) {
                SectionLabel(Self.coverageLabel(for: story.sources))
                    .padding(.top, MercurySpace.snug)

                VStack(alignment: .leading, spacing: 0) {
                    ForEach(story.sources) { source in
                        SourceRow(source: source) { openURL = $0 }
                        if source.id != story.sources.last?.id {
                            Rectangle()
                                .fill(MercuryColor.hairline.color)
                                .frame(height: 1)
                                .padding(.leading, MercurySpace.comfortable)
                        }
                    }
                }
                .mercuryCard(.raised)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// "Coverage · 3 outlets", or "Coverage · 4 articles from 3 outlets"
    /// when one outlet wrote more than once.
    ///
    /// Both numbers, when they differ, because the rows below are articles
    /// and the byline above counts outlets: a header naming only one of them
    /// would disagree with something else on the same screen.
    ///
    /// Taken from the SOURCES rather than from two counts, because the two
    /// counts did not measure the same set. Review of #68: an article with
    /// no outlet name counted as an article and not as an outlet, so one
    /// Reuters piece beside one nameless source read "2 articles from 1
    /// outlet" — crediting Reuters with an article it did not write. When
    /// any source is nameless, the outlet count cannot be stated honestly,
    /// so only the articles are.
    static func coverageLabel(for sources: [DigestSource]) -> String {
        func count(_ n: Int, _ noun: String) -> String {
            "\(n) \(noun)\(n == 1 ? "" : "s")"
        }
        let names = sources.map(\.sourceName)
        if names.contains(where: \.isEmpty) {
            return "Coverage · \(count(sources.count, "article"))"
        }
        let outlets = Set(names).count
        if outlets == sources.count {
            return "Coverage · \(count(outlets, "outlet"))"
        }
        return "Coverage · \(count(sources.count, "article")) from \(count(outlets, "outlet"))"
    }
}

/// One publication's coverage, tappable when there is something to open.
///
/// A big target, which is the part of Particle's page its publishers care
/// about: the row is the way back to the journalism, so the whole row opens
/// it, and the outlet's mark leads so the eye finds the publisher first.
///
/// An email source has no URL, structurally — `gmail.py` builds every email
/// `Item` with none, because a message in your inbox has no public address.
/// So a newsletter row is a statement of where the story came from, not a
/// link, and it says so rather than looking tappable and doing nothing.
struct SourceRow: View {
    let source: DigestSource
    let onOpen: (URL) -> Void

    var body: some View {
        Group {
            if let url = source.url {
                Button { onOpen(url) } label: { content(openable: true) }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens the article")
            } else {
                content(openable: false)
                    // Conditioned on the type. An email genuinely has no
                    // public address and saying so is useful; an RSS source
                    // with a missing URL is a gap in the data, and telling
                    // the user it came from their inbox would be false.
                    .accessibilityHint(
                        source.sourceType == "email"
                            ? "From your inbox — no link to open"
                            : "No link available")
            }
        }
    }

    private var isEmail: Bool { source.sourceType == "email" }

    private func content(openable: Bool) -> some View {
        HStack(alignment: .top, spacing: MercurySpace.base) {
            SourceStack(outlets: [source.sourceName], visible: 1)

            VStack(alignment: .leading, spacing: 2) {
                Text(source.sourceName)
                    .font(MercuryType.eyebrow)
                    .textCase(.uppercase)
                    .tracking(0.6)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                Text(source.title)
                    .font(MercuryType.cardBody)
                    .foregroundStyle(MercuryColor.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if isEmail {
                    Label("From your inbox", systemImage: "envelope")
                        .font(MercuryType.caption)
                        .foregroundStyle(MercuryColor.inkFaint.color)
                        .labelStyle(.titleAndIcon)
                }
            }
            Spacer(minLength: MercurySpace.snug)
            if openable {
                Image(systemName: "arrow.up.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .accessibilityHidden(true)
            }
        }
        .frame(minHeight: 44)
        .padding(MercurySpace.comfortable)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// `SFSafariViewController`, which keeps the reader inside Mercury and gives
/// them Reader mode, sharing and a Done button without any of it being built
/// here.
struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let config = SFSafariViewController.Configuration()
        config.entersReaderIfAvailable = true
        return SFSafariViewController(url: url, configuration: config)
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

/// `sheet(item:)` needs Identifiable, and a URL is its own identity here.
extension URL: @retroactive Identifiable {
    public var id: String { absoluteString }
}

#Preview("Story") {
    NavigationStack {
        StoryDetailView(
            story: DigestStory(
                id: "a",
                headline: "Northwind buys Halcyon for four point two billion",
                points: [
                    "Largest robotics deal on record, and the first above four billion.",
                    "Regulators in Brussels have already signalled a review.",
                    "Halcyon had no revenue in either of the last two years.",
                ],
                whyItMatters: "It sets a price for a company with no revenue, which "
                    + "every robotics founder will now cite.",
                sourceCount: 3,
                sources: [
                    DigestSource(sourceName: "Reuters", title: "Northwind to buy Halcyon",
                                 url: URL(string: "https://example.com/a")),
                    DigestSource(sourceName: "The Verge", title: "What Halcyon actually makes",
                                 url: URL(string: "https://example.com/b")),
                    DigestSource(sourceName: "Stratechery", title: "Monday letter",
                                 sourceType: "email"),
                ],
                coveredInAudio: true, turnIndex: 4),
            onPlay: { _ in }
        )
    }
}


private extension View {
    /// A hard top edge where the platform has one (iOS 26), so scrolled
    /// text stops under the floating back button instead of running beneath
    /// it: the points card's first letters sat under the button on the
    /// simulator (ROADMAP task 53). Earlier systems draw a bar background
    /// already.
    ///
    /// Guarded at COMPILE time as well as run time. `#available` is a runtime
    /// check, but the symbol has to exist in the SDK being built against, and
    /// CI builds with Xcode 16.4 (Swift 6.1, the iOS 18 SDK), where it does
    /// not: the first push of #76 failed there with "cannot find
    /// 'scrollEdgeEffectStyle' in scope". Swift 6.2 is Xcode 26's.
    @ViewBuilder
    func hardTopScrollEdge() -> some View {
        #if compiler(>=6.2)
        if #available(iOS 26.0, *) {
            scrollEdgeEffectStyle(.hard, for: .top)
        } else {
            self
        }
        #else
        self
        #endif
    }
}
