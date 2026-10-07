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
        // LAZY, and this is a real cost rather than a preference.
        //
        // A plain `VStack` builds every row immediately, and each row holds
        // an `AsyncImage` — so opening Home fired thirteen image downloads
        // at once before a single one was on screen. That went unnoticed
        // for as long as the deployed Worker had no `/digest` route and the
        // desk was always empty; the first morning it actually rendered,
        // Home got visibly slower to open.
        //
        // `TranscriptView` deliberately does NOT use this — there a lazy
        // stack measures rows during an animated `scrollTo` and the text
        // shakes. Nothing scrolls this programmatically.
        LazyVStack(alignment: .leading, spacing: 0) {
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
            // The picture is a BACKGROUND, not a sibling in the stack.
            //
            // As a ZStack sibling it decided the card's size: `AsyncImage`
            // with `.aspectRatio(contentMode: .fill)` deliberately overflows
            // the size it is offered, and a ZStack takes the union of its
            // children — so the stack grew to the image's width, roughly
            // half again the screen. The headline was then laid out at THAT
            // width, and `.clipShape` cut the overflow off both ends. On
            // screen it read as two bugs: a headline sliced open at both
            // edges, and an eyebrow reading "· 2 hours ago" because
            // "2 articles" had been pushed off the left of the card.
            //
            // A background is measured AFTER its content and never
            // contributes to layout, which is exactly the relationship a
            // photograph behind text should have.
            VStack(alignment: .leading, spacing: MercurySpace.tight) {
                DeskEyebrow(story: story, onDarkGround: true)

                Text(story.headline)
                    .font(MercuryType.headline)
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
            // MINIMUM height, not fixed. At accessibility type sizes a
            // three-line headline plus a two-line standfirst plus 32pt of
            // padding does not fit in 320pt, and a fixed height clips the
            // text rather than the card growing to hold it.
            .frame(maxWidth: .infinity, minHeight: 320, alignment: .bottomLeading)
            .background {
                ZStack {
                    artwork
                    // A scrim, not a flat tint. Text over a photograph needs
                    // the contrast guaranteed rather than hoped for, and a
                    // gradient keeps the top of the image readable as an
                    // image.
                    LinearGradient(
                        colors: [.black.opacity(0.0), .black.opacity(0.75)],
                        startPoint: .center, endPoint: .bottom
                    )
                }
            }
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

    /// Mercury's own mark, not a blank panel.
    ///
    /// A newsletter-only story HAS no picture — rule 5 discards the body its
    /// images live in — so on a quiet morning this is the ORDINARY case, not
    /// an error. A flat wash read as an image that had failed to load; the
    /// sunrise says "this story has no photograph" in the app's own voice,
    /// and it is the same mark as the icon and the lock screen.
    private var accentGround: some View {
        MercuryMark()
    }
}

/// The outlets' marks, then "REUTERS +3 · 2H AGO", with the waveform when
/// the hosts covered it.
///
/// It said "4 ARTICLES", which counted cluster MEMBERS — a newsletter that
/// wrote about the story twice was two of the four — and a bare count asks
/// to be trusted. Particle puts the publishers on every story in the feed,
/// because who reported something is the first thing you judge it by; this
/// does the same, with the most authoritative outlet named and drawn first.
struct DeskEyebrow: View {
    let story: DigestStory
    var onDarkGround: Bool = false

    private var outlets: [String] { SourceAttribution.outlets(of: story) }

    var body: some View {
        HStack(spacing: MercurySpace.snug) {
            if !outlets.isEmpty {
                SourceStack(outlets: outlets, onDarkGround: onDarkGround)
            }
            HStack(spacing: MercurySpace.tight) {
                if story.coveredInAudio {
                    Image(systemName: "waveform")
                        .font(.caption2)
                        .accessibilityLabel("In your briefing")
                }
                Text(line)
                    .font(MercuryType.eyebrow)
                    .textCase(.uppercase)
                    .tracking(0.6)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(
            onDarkGround ? AnyShapeStyle(.white.opacity(0.9))
                         : AnyShapeStyle(MercuryColor.inkFaint.color)
        )
    }

    private var line: String {
        [SourceAttribution.compact(outlets), StoryDetailView.age(of: story)]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
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
                        MercuryMark()
                    }
                }
            } else {
                MercuryMark()
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


/// The sunrise, as a view.
///
/// Stands in wherever a story has no picture of its own, which is the
/// ordinary case rather than a failure: rule 5 discards the email bodies
/// that images live in, so a newsletter-only story has nothing to show. A
/// tinted rectangle read as a photograph that had failed to load — this
/// reads as "no photograph", in the same mark as the app icon and the lock
/// screen card.
///
/// Drawn from `MercuryArtwork`'s geometry rather than by loading its bitmap:
/// a thumbnail is 72pt and a lead card is the full width, and rasterising
/// the larger one to fill the smaller wastes memory on every row.
struct MercuryMark: View {
    var body: some View {
        // `Canvas`, not stacked shapes with offsets and a mask.
        //
        // The first cut positioned a `Circle` and masked its lower half, and
        // the mask was taller than the circle — so it clipped nothing and
        // every thumbnail showed a full disc floating above the rule rather
        // than a sunrise. Three interacting offsets, none of them wrong on
        // their own.
        //
        // Drawing the clip explicitly removes the arithmetic: the sky is a
        // rectangle, the sun is a circle inside it, and the rule sits on the
        // boundary. It also matches `scripts/make_app_icon.py` line for
        // line, which is the point — this is the same mark.
        Canvas { context, size in
            let side = max(size.width, size.height)
            let horizon = size.height * MercuryArtwork.horizonY
            let radius = side * MercuryArtwork.sunRadius

            context.fill(Path(CGRect(origin: .zero, size: size)),
                         with: .color(Color(MercuryArtwork.paper)))

            let accent = GraphicsContext.Shading.color(
                Color(MercuryArtwork.accent))

            // The sun: a disc CLIPPED to the sky, so it reads as rising out
            // of the horizon rather than resting on it.
            context.drawLayer { sky in
                sky.clip(to: Path(CGRect(x: 0, y: 0,
                                         width: size.width, height: horizon)))
                sky.fill(
                    Path(ellipseIn: CGRect(
                        x: size.width / 2 - radius, y: horizon - radius,
                        width: radius * 2, height: radius * 2)),
                    with: accent)
            }

            // The masthead device: a heavy rule and a hairline under it.
            let inset = size.width * MercuryArtwork.ruleInset
            let width = size.width - inset * 2
            context.fill(
                Path(CGRect(x: inset, y: horizon, width: width,
                            height: side * MercuryArtwork.ruleWeight)),
                with: accent)
            context.fill(
                Path(CGRect(
                    x: inset,
                    y: horizon + side * (MercuryArtwork.ruleWeight
                                         + MercuryArtwork.hairlineGap),
                    width: width,
                    height: max(side * MercuryArtwork.hairlineWeight, 0.5))),
                with: accent)
        }
        .accessibilityHidden(true)
    }
}
