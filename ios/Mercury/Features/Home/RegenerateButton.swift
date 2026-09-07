import SwiftUI

/// Rebuild today's briefing from mail that has arrived since.
///
/// The briefing is made overnight, so by mid-morning it describes an inbox
/// that has moved on. This is the way to catch it up.
///
/// **It asks first, and says why.** This is the only action in Mercury that
/// costs real money per press — a full Gemini and ElevenLabs generation —
/// and a button that quietly spends is a button people learn to distrust.
/// The confirmation names the cost and the remaining allowance rather than
/// asking "are you sure?", which tells nobody anything.
struct RegenerateButton: View {
    /// Where it is being drawn, which changes only its LABEL.
    ///
    /// The confirmation, the cost sentence and the disabled rule are
    /// identical in both — a control that spends money must not become
    /// quieter about it just because it moved into a toolbar.
    enum Style {
        /// A full-width row with words on it, for the body of a page.
        case row
        /// A 44pt glyph beside the other toolbar items.
        case toolbar
    }

    let isRunning: Bool
    /// How many rebuilds are left today, once the server has said. Nil
    /// before the first attempt — the count is not worth a request of its
    /// own, so the button simply does not claim a number it does not have.
    let remaining: Int?
    var style: Style = .row
    let onConfirm: () -> Void

    @State private var asking = false

    var body: some View {
        Button { asking = true } label: {
            switch style {
            case .row:
                HStack(spacing: MercurySpace.snug) {
                    glyph
                    Text(isRunning ? "Rebuilding…" : "Rebuild from my latest mail")
                        .font(MercuryType.cardBody)
                    Spacer(minLength: 0)
                }
                .foregroundStyle(MercuryColor.inkMuted.color)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            case .toolbar:
                glyph
                    .foregroundStyle(MercuryColor.inkMuted.color)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
        }
        .buttonStyle(.plain)
        .disabled(isRunning || remaining == 0)
        .confirmationDialog(
            "Rebuild today's briefing?",
            isPresented: $asking,
            titleVisibility: .visible
        ) {
            Button("Rebuild", role: .destructive, action: onConfirm)
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(confirmationMessage)
        }
        .accessibilityLabel("Rebuild from my latest mail")
        .accessibilityHint(
            isRunning ? "A rebuild is already running" : "Asks before starting")
    }

    /// Spins while a rebuild is outstanding, so the toolbar form still says
    /// something is happening without room for the word "Rebuilding".
    @ViewBuilder
    private var glyph: some View {
        if isRunning {
            ProgressView()
                .controlSize(.small)
                .tint(MercuryColor.inkMuted.color)
        } else {
            Image(systemName: "arrow.clockwise")
        }
    }

    /// Names the cost, the wait and what is left — in that order, because
    /// that is the order the questions occur to someone about to press it.
    ///
    /// Not private, so a test can assert on the sentence the user actually
    /// reads rather than on a flag that is supposed to produce it.
    var confirmationMessage: String {
        var lines = [
            "This re-reads your mail and calendar now and makes a new "
            + "briefing. It takes a few minutes and costs a full generation.",
        ]
        // The count arrives with the first response of the day, so the
        // FIRST press — the one where someone is deciding whether this is a
        // thing they can undo — is exactly the press that had no number.
        // Saying nothing read as "unlimited"; this says the shape of the
        // limit without inventing a figure the server owns.
        if let remaining {
            lines.append(
                remaining == 1
                    ? "One rebuild left today."
                    : "\(remaining) rebuilds left today.")
        } else {
            lines.append("Only a few are allowed each day.")
        }
        return lines.joined(separator: "\n\n")
    }
}

#Preview("Regenerate") {
    VStack(alignment: .leading, spacing: 24) {
        RegenerateButton(isRunning: false, remaining: nil, onConfirm: {})
        RegenerateButton(isRunning: false, remaining: 1, onConfirm: {})
        RegenerateButton(isRunning: true, remaining: 2, onConfirm: {})
        RegenerateButton(isRunning: false, remaining: 0, onConfirm: {})
    }
    .mercuryGutter()
    .background(MercuryColor.surface.color)
}
