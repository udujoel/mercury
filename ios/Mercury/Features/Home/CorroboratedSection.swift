import SwiftUI

/// The stories more than one outlet carried, named rather than counted.
///
/// Sits above the newsletters section: what several outlets independently
/// thought worth covering is a stronger claim on your attention than what
/// one sender emailed, and the page should be ordered by that.
struct CorroboratedSection: View {

    let items: [CorroboratedStories.Item]
    let summary: String?
    let onSelect: (DigestStory) -> Void

    /// Six rows. The list is already short by construction — about fifteen
    /// on a live morning — and this section's job is to be the top of the
    /// page rather than the whole of it.
    static let visibleRows = 6

    private var visible: [CorroboratedStories.Item] {
        Array(items.prefix(Self.visibleRows))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let summary {
                Text(summary)
                    .font(MercuryType.figureCaption)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .padding(.bottom, MercurySpace.snug)
            }

            ForEach(visible) { item in
                Button { onSelect(item.story) } label: {
                    VStack(alignment: .leading, spacing: MercurySpace.tight) {
                        Text(item.story.headline)
                            .font(MercuryType.cardBody)
                            .foregroundStyle(MercuryColor.ink.color)
                            .multilineTextAlignment(.leading)

                        // Named, not counted. "3 sources" asks the reader to
                        // trust the number; naming them lets the reader judge
                        // it, which is the point of a corroboration claim.
                        HStack(alignment: .top, spacing: MercurySpace.snug) {
                            SourceStack(outlets: item.outlets)
                            Text(CorroboratedStories.attribution(item.outlets))
                                .font(MercuryType.figureCaption)
                                .foregroundStyle(MercuryColor.inkFaint.color)
                                .multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, MercurySpace.snug)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityElement(children: .combine)

                if item.id != visible.last?.id {
                    Divider().overlay(MercuryColor.hairline.color)
                }
            }

            let hidden = items.count - visible.count
            if hidden > 0 {
                Text("and \(hidden) more on the desk")
                    .font(MercuryType.figureCaption)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .padding(.top, MercurySpace.snug)
            }
        }
    }
}
