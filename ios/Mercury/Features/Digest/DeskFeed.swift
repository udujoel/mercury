import SwiftUI

/// The desk, in Particle's shape.
///
/// What this replaces: a horizontal row of 180pt cards. That row said "there
/// is more here" and showed almost none of it — two and a half headlines at a
/// size you had to lean in for, on a screen with a whole column of width
/// going spare.
///
/// Particle's answer, and now this one: most stories are a **thumbnail row** —
/// image left, source count and age above, headline in two or three lines you
/// can actually read. Occasionally one is promoted to a **lead card**: full
/// width, big image, headline over it, a sentence of standfirst.
///
/// The promotion is not decoration. A briefing where every story looks equally
/// important is a feed, and the reason to have a desk rather than a feed is
/// that something is the biggest story of the morning.
struct DeskFeed: View {
    /// EVERY story on the desk, uncapped.
    ///
    /// The cap is applied here rather than by the caller, and that is not a
    /// tidying preference: choosing the lead from an already-truncated list
    /// picks the most-covered story of the first twelve, not of the morning.
    /// A day where coverage concentrates on something that happens to sort
    /// thirteenth would lead with the wrong story and look deliberate.
    let stories: [DigestStory]
    /// How many rows to show beneath the lead. Enough to feel like a desk,
    /// few enough to still be a briefing — this morning's digest holds 174
    /// browsable stories, and rendering them all is the feed Mercury exists
    /// to replace.
    var limit: Int = 12
    let onSelect: (DigestStory) -> Void

    /// The lead is the most-covered story the hosts actually discussed.
    ///
    /// Source count is the honest signal here: it is the number of
    /// publications that independently thought the story worth writing about,
    /// which is exactly what "big" means on a news morning. Falling back to
    /// the first covered story keeps a lead on a quiet day, and nil on a day
    /// with nothing covered means no lead rather than an arbitrary one.
    private var lead: DigestStory? {
        stories.filter(\.coveredInAudio).max { $0.sourceCount < $1.sourceCount }
    }

    private var rest: [DigestStory] {
        let others = lead.map { l in stories.filter { $0.id != l.id } } ?? stories
        return Array(others.prefix(limit))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Two rows, then the lead, then the remainder. Putting the lead
            // at the very top would push the play button off the screen,
            // and the play button is still what this app is for.
            ForEach(rest.prefix(2)) { story in
                DeskRow(story: story) { onSelect(story) }
                divider
            }

            if let lead {
                LeadCard(story: lead) { onSelect(lead) }
                    .padding(.vertical, MercurySpace.snug)
            }

            // `enumerated`, so the "is this the first one" test is an index
            // comparison rather than re-slicing the same array on every
            // iteration to look at its head.
            ForEach(Array(rest.dropFirst(2).enumerated()), id: \.element.id) { i, story in
                if i > 0 { divider }
                DeskRow(story: story) { onSelect(story) }
            }
        }
    }

    private var divider: some View {
        Rectangle()
            .fill(MercuryColor.hairline.color)
            .frame(height: 1)
    }
}

/// One story as a row: thumbnail, then the reading matter.
struct DeskRow: View {
    @Environment(\.dynamicTypeSize) private var typeSize

    let story: DigestStory
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(alignment: .top, spacing: MercurySpace.snug) {
                Thumbnail(url: story.imageURL, side: side)

                VStack(alignment: .leading, spacing: MercurySpace.tight) {
                    DeskEyebrow(story: story)

                    Text(story.headline)
                        .font(MercuryType.cardTitle)
                        .foregroundStyle(MercuryColor.ink.color)
                        .multilineTextAlignment(.leading)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, MercurySpace.snug)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the story")
    }

    /// Grows with the text, so the thumbnail does not shrink into a stamp
    /// beside a headline set three times its height.
    private var side: CGFloat { typeSize.isAccessibilitySize ? 104 : 72 }
}

/// The morning's biggest story, given the width to say so.
struct LeadCard: View {
    let story: DigestStory
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            ZStack(alignment: .bottomLeading) {
                artwork

                // A scrim, not a flat tint. Text over a photograph needs the
                // contrast to be guaranteed rather than hoped for, and a
                // gradient keeps the top of the image readable as an image.
                LinearGradient(
                    colors: [.black.opacity(0.0), .black.opacity(0.75)],
                    startPoint: .center, endPoint: .bottom
                )

                VStack(alignment: .leading, spacing: MercurySpace.tight) {
                    DeskEyebrow(story: story, onDarkGround: true)

                    Text(story.headline)
                        .font(MercuryType.greeting)
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.leading)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)

                    if let standfirst {
                        Text(standfirst)
                            .font(MercuryType.cardBody)
                            .foregroundStyle(.white.opacity(0.85))
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(MercurySpace.comfortable)
            }
            // MINIMUM, not fixed. At accessibility type sizes a three-line
            // headline plus a two-line standfirst plus 32pt of padding does
            // not fit in 320pt, and a fixed height clips the text rather
            // than the card growing to hold it.
            .frame(minHeight: 320)
            .frame(maxWidth: .infinity)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the story")
    }

    /// The first bullet, or the paragraph. Bullets are written for the detail
    /// view and the first one is reliably the most important fact, which is
    /// exactly what a standfirst wants.
    private var standfirst: String? {
        story.points.first ?? story.summary
    }

    @ViewBuilder
    private var artwork: some View {
        if let url = story.imageURL {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().aspectRatio(contentMode: .fill)
                default:
                    accentGround
                }
            }
        } else {
            accentGround
        }
    }

    /// The day's accent rather than grey. A newsletter-only story HAS no
    /// picture — rule 5 discards the body its images live in — so on a quiet
    /// morning this is the ordinary case, and grey would read as an image
    /// that failed to load.
    private var accentGround: some View {
        LinearGradient(
            colors: [
                MercuryColor.accent.color.opacity(0.55),
                MercuryColor.accent.color.opacity(0.85),
            ],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }
}

/// "4 ARTICLES · 2H AGO", with the waveform when the hosts covered it.
struct DeskEyebrow: View {
    let story: DigestStory
    var onDarkGround: Bool = false

    var body: some View {
        HStack(spacing: MercurySpace.tight) {
            if story.coveredInAudio {
                Image(systemName: "waveform")
                    .font(.caption2)
            }
            Text(line)
                .font(MercuryType.eyebrow)
                .textCase(.uppercase)
                .tracking(0.6)
            Spacer(minLength: 0)
        }
        .foregroundStyle(
            onDarkGround ? AnyShapeStyle(.white.opacity(0.9))
                         : AnyShapeStyle(MercuryColor.inkFaint.color)
        )
    }

    private var line: String {
        var parts: [String] = []
        switch story.sources.count {
        case 0: break
        case 1: parts.append(story.sources[0].sourceName)
        default: parts.append("\(story.sources.count) articles")
        }
        if let age { parts.append(age) }
        return parts.joined(separator: " · ")
    }

    /// "2h ago". Relative, because on a morning briefing "how fresh" is the
    /// only thing a timestamp is being asked.
    private var age: String? {
        guard let published = story.sources.compactMap(\.publishedAt).max()
        else { return nil }
        return published.formatted(.relative(presentation: .numeric))
    }
}

/// A square image, or the accent where one would be.
struct Thumbnail: View {
    let url: URL?
    let side: CGFloat

    var body: some View {
        ZStack {
            if let url {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().aspectRatio(contentMode: .fill)
                    default:
                        MercuryColor.accent.color.opacity(0.18)
                    }
                }
            } else {
                MercuryColor.accent.color.opacity(0.18)
            }
        }
        .frame(width: side, height: side)
        .clipped()
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityHidden(true)
    }
}

#Preview("Desk") {
    ScrollView {
        DeskFeed(
            stories: [
                DigestStory(id: "a", headline: "Jobs jump pushes Fed hike odds and pulls "
                            + "Bitcoin back below $80,000", sourceCount: 4,
                            sources: [DigestSource(sourceName: "Reuters", title: "t")]),
                DigestStory(id: "b", headline: "Invisible Unicode used to hide phishing "
                            + "keywords, Microsoft warns", sourceCount: 4),
                DigestStory(id: "c",
                            headline: "OpenAI agents hijacked German wiki, researchers say",
                            points: ["Published research links thousands of edits to "
                                     + "Azure and OpenAI-linked infrastructure."],
                            sourceCount: 29, coveredInAudio: true, turnIndex: 3),
                DigestStory(id: "d", headline: "Southeast Asia blockchain funding climbs",
                            sourceCount: 4),
            ],
            onSelect: { _ in }
        )
        .mercuryGutter()
    }
    .background(MercuryColor.surface.color)
}
