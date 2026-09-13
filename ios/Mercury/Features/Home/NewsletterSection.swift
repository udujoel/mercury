import SwiftUI

/// The newsletters that arrived, under the desk.
///
/// Sender-led on purpose, and the roadmap's "topic-led rather than
/// sender-led" is deliberately NOT attempted here — see docs/LOG.md,
/// 2026-09-12. The `topics` the backend emits are single lowercase words,
/// so they are categories (`ai`, `society`, `history`) rather than subjects,
/// and a section headed "AI — 11 senders" answers nothing. Subjects need the
/// extraction to change, which is task 17. "Who wrote to me this morning,
/// and did any of it reach the briefing" is a question this data can already
/// answer honestly, so it is the one this section asks.
struct NewsletterSection: View {

    let senders: [NewsletterDesk.Sender]
    let summary: String?
    /// Opens the story this sender contributed to. Nil when they contributed
    /// to none, which makes the row non-interactive rather than a control
    /// that does nothing — `BriefingDigest.Row`'s rule.
    let onOpen: (String) -> Void

    private var visible: [NewsletterDesk.Sender] {
        Array(senders.prefix(NewsletterDesk.visibleSenders))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let summary {
                Text(summary)
                    .font(MercuryType.figureCaption)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .padding(.bottom, MercurySpace.snug)
            }

            ForEach(visible) { sender in
                SenderRow(sender: sender, onOpen: onOpen)
                if sender.id != visible.last?.id {
                    Divider().overlay(MercuryColor.hairline.color)
                }
            }

            let hidden = NewsletterDesk.remainder(senders)
            if hidden > 0 {
                Text("and \(hidden) more sender\(hidden == 1 ? "" : "s")")
                    .font(MercuryType.figureCaption)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .padding(.top, MercurySpace.snug)
            }
        }
    }
}

private struct SenderRow: View {
    let sender: NewsletterDesk.Sender
    let onOpen: (String) -> Void

    private var storyId: String? { sender.storyIds.first }

    var body: some View {
        // A row that can open something is a button; a row that cannot is
        // text. Not a disabled button — a control greyed out for reasons the
        // user cannot see reads as a fault, and this is an ordinary state:
        // a newsletter that clustered with nothing browsable simply has
        // nowhere to go.
        if let storyId {
            Button { onOpen(storyId) } label: { content }
                .buttonStyle(.plain)
                .accessibilityHint("Opens the story this contributed to")
        } else {
            content
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: MercurySpace.tight) {
            HStack(alignment: .firstTextBaseline) {
                Text(sender.id)
                    .font(MercuryType.cardBody)
                    .foregroundStyle(MercuryColor.ink.color)
                Spacer(minLength: MercurySpace.snug)
                if sender.reachedTheBriefing {
                    // The signal the section exists for, and the only place
                    // the accent appears here: of everything that arrived,
                    // this is what you actually heard.
                    Text("In the briefing")
                        .font(MercuryType.figureCaption)
                        .foregroundStyle(MercuryColor.accent.color)
                }
            }

            // The subject lines themselves, capped. A sender who sent four
            // things gets named once, not four times.
            ForEach(sender.items.prefix(2), id: \.id) { item in
                Text(item.subject)
                    .font(MercuryType.figureCaption)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .lineLimit(2)
            }
            if sender.items.count > 2 {
                Text("and \(sender.items.count - 2) more")
                    .font(MercuryType.figureCaption)
                    .foregroundStyle(MercuryColor.inkFaint.color)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, MercurySpace.snug)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
