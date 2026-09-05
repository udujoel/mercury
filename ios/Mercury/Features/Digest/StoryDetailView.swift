import SafariServices
import SwiftUI

/// One story, opened.
///
/// The reading half of Mercury, and the thing the digest was built to feed:
/// an image, the reporting in bullets, why it matters, every publication that
/// covered it, and — when the hosts discussed it — a way into the audio at
/// the exact turn where they did.
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

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: MercurySpace.comfortable) {
                lead
                headline
                if !story.points.isEmpty {
                    points
                } else if let summary = story.summary {
                    Text(summary)
                        .font(MercuryType.cardBody)
                        .foregroundStyle(MercuryColor.inkMuted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let why = story.whyItMatters { whyItMatters(why) }
                if let onPlay, let index = story.turnIndex { listen(onPlay, index) }
                sources
            }
            .mercuryGutter()
            .padding(.vertical, MercurySpace.comfortable)
        }
        .background(MercuryColor.surface.color.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $openURL) { url in
            // In-app, not a jump to Safari. The morning ritual is the app;
            // being thrown out of it to read one article and having to find
            // your way back is how a briefing becomes a browsing session.
            SafariView(url: url)
                .ignoresSafeArea()
        }
    }

    @ViewBuilder
    private var lead: some View {
        if let url = story.imageURL {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().aspectRatio(contentMode: .fill)
                case .failure:
                    // A publisher's CDN refusing a hotlink is ordinary. The
                    // story reads perfectly well without its picture, so the
                    // space closes up rather than showing a broken frame.
                    // `EmptyView`, not a zero-height Color: the latter still
                    // participates in layout and leaves the frame's spacing
                    // behind it.
                    EmptyView()
                default:
                    MercuryColor.accent.color.opacity(0.12)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(maxHeight: 220)
            .clipped()
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .accessibilityHidden(true)
        }
    }

    private var headline: some View {
        Text(story.headline)
            .font(MercuryType.greeting)
            .foregroundStyle(MercuryColor.ink.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityAddTraits(.isHeader)
    }

    private var points: some View {
        VStack(alignment: .leading, spacing: MercurySpace.snug) {
            ForEach(Array(story.points.enumerated()), id: \.offset) { _, point in
                HStack(alignment: .firstTextBaseline, spacing: MercurySpace.snug) {
                    // Drawn here, not carried in the string — the backend
                    // strips any marker the model added, so this is the only
                    // place a bullet exists.
                    Circle()
                        .fill(MercuryColor.accent.color)
                        .frame(width: 4, height: 4)
                        .accessibilityHidden(true)
                    Text(point)
                        .font(MercuryType.cardBody)
                        .foregroundStyle(MercuryColor.ink.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func whyItMatters(_ text: String) -> some View {
        VStack(alignment: .leading, spacing: MercurySpace.tight) {
            SectionLabel("Why it matters")
            Text(text)
                .font(MercuryType.cardBody)
                .foregroundStyle(MercuryColor.inkMuted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func listen(_ onPlay: @escaping (Int) -> Void, _ index: Int) -> some View {
        Button { onPlay(index) } label: {
            HStack(spacing: MercurySpace.snug) {
                Image(systemName: "waveform")
                Text("Hear the hosts on this")
                    .font(MercuryType.cardBody)
                Spacer(minLength: 0)
            }
            .foregroundStyle(MercuryColor.accent.color)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint("Plays the briefing from where this story is discussed")
    }

    @ViewBuilder
    private var sources: some View {
        // Counted from what is actually RENDERED, not from `source_count`.
        // A legacy story with a count of 3 and an empty array said
        // "3 sources" above nothing at all, which implies coverage exists
        // and is being withheld. The section disappears instead.
        if !story.sources.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                SectionLabel(
                    story.sources.count == 1 ? "1 source" : "\(story.sources.count) sources"
                )
                .padding(.bottom, MercurySpace.tight)

                ForEach(story.sources) { source in
                    SourceRow(source: source) { openURL = $0 }
                    if source.id != story.sources.last?.id {
                        Rectangle()
                            .fill(MercuryColor.hairline.color)
                            .frame(height: 1)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// One publication's coverage, tappable when there is something to open.
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
                    .accessibilityHint("From your inbox — no link to open")
            }
        }
    }

    private func content(openable: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: MercurySpace.snug) {
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
            }
            Spacer(minLength: MercurySpace.snug)
            if openable {
                Image(systemName: "arrow.up.right")
                    .font(.footnote)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .accessibilityHidden(true)
            }
        }
        .frame(minHeight: 44)
        .padding(.vertical, MercurySpace.tight)
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
