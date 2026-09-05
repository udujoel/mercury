import SwiftUI

/// The desk: what arrived, as things you can open.
///
/// Home has said "14 stories" since the digest shipped and shown none of
/// them. This is the other half — the stories themselves, as cards you can
/// scroll sideways and tap, which is the Particle shape and the reason the
/// digest artifact exists at all (ADR-013).
///
/// Horizontal rather than a vertical list, deliberately. Home's job is still
/// the play button; a full-width list of fourteen stories buries it under a
/// scroll, and the whole point of a briefing is that you press play instead
/// of reading a feed. A row you can flick through says "there is more here"
/// without competing for the screen.
struct StoryCards: View {
    let stories: [DigestStory]
    let onSelect: (DigestStory) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: MercurySpace.snug) {
                ForEach(stories) { story in
                    Button { onSelect(story) } label: {
                        StoryCard(story: story)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, MercurySpace.gutter)
            .padding(.vertical, MercurySpace.tight)
        }
    }
}

/// One story, at a size that fits two-and-a-bit on screen.
///
/// The "and a bit" is the point: a card cut off at the right edge is what
/// tells you the row scrolls, and it costs nothing to arrange.
struct StoryCard: View {
    @Environment(\.dynamicTypeSize) private var typeSize

    let story: DigestStory

    /// Fixed width, not a fraction of the screen. A card that reflows with
    /// the device makes the headline break differently on every phone and
    /// the row stop looking like a row.
    private var width: CGFloat { typeSize.isAccessibilitySize ? 260 : 180 }

    var body: some View {
        VStack(alignment: .leading, spacing: MercurySpace.tight) {
            artwork

            Text(story.headline)
                .font(MercuryType.cardBody)
                .foregroundStyle(MercuryColor.ink.color)
                .multilineTextAlignment(.leading)
                // Three lines, then truncate. A headline is already a
                // summary; letting it run makes cards of different heights
                // and the row ragged.
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)

            footnote
        }
        .frame(width: width, alignment: .leading)
        .padding(MercurySpace.snug)
        .mercuryCard(.raised)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityHint("Opens the story")
    }

    /// The image, or a band of the day's accent where one would be.
    ///
    /// Not a grey placeholder box: a newsletter-only story simply has no
    /// picture — rule 5 discards the body its images live in — and on a
    /// quiet morning that is most of the desk. A row of grey rectangles
    /// reads as broken loading; a coloured band reads as a design.
    @ViewBuilder
    private var artwork: some View {
        ZStack {
            if let url = story.imageURL {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().aspectRatio(contentMode: .fill)
                    default:
                        // Covers empty AND failure. A publisher's CDN 403ing
                        // a hotlink is ordinary, and it should look the same
                        // as having no image rather than like an error.
                        MercuryColor.accent.color.opacity(0.18)
                    }
                }
            } else {
                MercuryColor.accent.color.opacity(0.18)
            }
        }
        .frame(height: 96)
        .frame(maxWidth: .infinity)
        .clipped()
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityHidden(true)
    }

    private var footnote: some View {
        HStack(spacing: MercurySpace.tight) {
            if story.coveredInAudio {
                // The one thing that distinguishes a story you can HEAR from
                // one you can only read, which is the distinction this whole
                // app is about.
                Image(systemName: "waveform")
                    .font(.caption2)
                    .foregroundStyle(MercuryColor.accent.color)
            }
            Text(sourceLine)
                .font(MercuryType.figureCaption)
                .foregroundStyle(MercuryColor.inkFaint.color)
            Spacer(minLength: 0)
        }
    }

    private var sourceLine: String {
        story.sourceCount == 1
            ? story.sources.first?.sourceName ?? "1 source"
            : "\(story.sourceCount) sources"
    }

    private var accessibilityLabel: String {
        var parts = [story.headline, sourceLine]
        if story.coveredInAudio { parts.append("in the briefing") }
        return parts.joined(separator: ", ")
    }
}

#Preview("Desk") {
    StoryCards(
        stories: [
            DigestStory(
                id: "a", headline: "Northwind buys Halcyon for four point two billion",
                sourceCount: 4, coveredInAudio: true, turnIndex: 3),
            DigestStory(
                id: "b", headline: "EU delays the single sign-on mandate",
                sources: [DigestSource(sourceName: "Stratechery", title: "Monday")]),
            DigestStory(id: "c", headline: "Fusion milestone at Culham", sourceCount: 2),
        ],
        onSelect: { _ in }
    )
    .background(MercuryColor.surface.color)
}
