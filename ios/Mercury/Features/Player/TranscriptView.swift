import SwiftUI

/// The sentence being spoken, and the speaker mark under it.
///
/// PRD 3.4's argument survives ADR-012 intact: the text *is* the visual
/// composition, which is what makes the screen feel designed rather than
/// functional. What changed is the execution — a serif line set ragged-right
/// against a wide margin, the way a printed pull-quote is set, instead of
/// large centred sans over a full-bleed ambient wash. Centring earns its place
/// when the text floats in a dark field with nothing to align to; it stops
/// earning it the moment there is a margin.
///
/// The ambient background went with it. On paper the composition is the type
/// and the white space around it, and a coloured wash behind a serif pull
/// quote is the register we are leaving.
struct TranscriptView: View {
    @Environment(\.mercuryMotion) private var motion

    let turn: Turn?

    var body: some View {
        VStack(alignment: .leading, spacing: MercurySpace.gutter) {
            Spacer(minLength: 0)

            Text(turn?.text ?? "")
                .font(MercuryType.transcript)
                // Ragged right, per ADR-012.
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .lineSpacing(MercuryType.voiceLineSpacing)
                .foregroundStyle(MercuryColor.ink.color)
                .fixedSize(horizontal: false, vertical: true)

            if let speaker = turn?.speaker {
                Text("— \(speaker)")
                    .font(MercuryType.eyebrow)
                    .textCase(.uppercase)
                    .tracking(0.6)
                    .foregroundStyle(MercuryColor.inkFaint.color)
                    .accessibilityLabel("Host \(speaker)")
            }

            Spacer(minLength: 0)
        }
        .mercuryGutter()
        .frame(maxWidth: .infinity, alignment: .leading)
        // 220ms, never a hard cut (PRD 3.4). A cross-fade is already
        // motion-free, so `resolve` passes it through unchanged rather than
        // substituting for it — see `MercuryMotion.resolved`.
        .id(turn?.id)
        .transition(.opacity)
        .animation(motion.resolve(MercuryMotion.crossfade), value: turn?.id)
        .accessibilityElement(children: .combine)
    }
}

#Preview {
    TranscriptView(
        turn: Turn(speaker: "A", segment: "inbox",
                   text: "And a bit of personal celebration, too — Akpet's birthday.",
                   startMs: 0, endMs: 4000)
    )
    .background(MercuryColor.surface.color)
}
