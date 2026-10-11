import SwiftUI

/// Who covered a story, the way Particle shows it: a small overlapping row of
/// outlet marks, then the outlets named.
///
/// The desk said "4 ARTICLES" in an eyebrow, which is a count of cluster
/// members rather than of publications, and a count asks the reader to trust
/// it. Particle's answer is to put the publishers themselves under every
/// summary, prominently, because who reported something is half of whether to
/// believe it. Mercury already ranks outlets by authority on the backend, so
/// the first mark in the row is the most authoritative one.
///
/// The marks are monograms rather than logos. Logos would mean fetching a
/// favicon per publisher on every row, hotlinked from sites that refuse
/// hotlinks often enough that `LeadCard` already has a fallback for it, and
/// ADR-012 is explicit that the edition has one colour: forty outlets each
/// bringing their own brand red would make the desk a sticker sheet. A
/// monogram in ink on paper carries the identity without the hue.
enum SourceAttribution {

    /// Words that never earn a letter. "The New York Times" is NY, not TN.
    ///
    /// No "&": the name is split on everything that is not a letter or a
    /// digit BEFORE this list is consulted, so a punctuation entry here
    /// could never match.
    private static let skipped: Set<String> = ["the", "a", "an", "of", "and"]

    /// The longest acronym kept whole. Four, because CNBC and TLDR are four
    /// letters and "CNB" or "TLD" would be a different outlet — the reason
    /// to keep acronyms at all. The mark's text scales down to fit.
    static let acronymLimit = 4

    /// One to four letters standing in for an outlet's logo.
    ///
    /// An acronym keeps its letters ("BBC", "NPR", "CNBC"), because that is
    /// how those outlets write their own names and a single "B" would be
    /// no one. Otherwise the initials of the first two significant
    /// words, so "Financial Times" is FT and "The Verge" is V. Never empty:
    /// a name made only of punctuation still gets a mark, so the row never
    /// draws a blank disc that reads as a failed image.
    static func monogram(for name: String) -> String {
        let words = name
            .split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .map(String.init)
            .filter { !skipped.contains($0.lowercased()) }

        guard let first = words.first else { return "•" }

        // At least one capital, or "1440" — a newsletter named for a
        // number — would be read as an acronym and drawn as digits.
        if first.count >= 2, first.contains(where: \.isUppercase),
           first.allSatisfy({ $0.isUppercase || $0.isNumber }) {
            return String(first.prefix(acronymLimit))
        }

        let initials = words.prefix(2).compactMap(\.first).map { String($0) }
        return initials.joined().uppercased()
    }

    /// Distinct outlet names, in the backend's authority order.
    ///
    /// Shares `CorroboratedStories.distinctOutlets` rather than counting
    /// `sources`, because those are cluster MEMBERS: a newsletter that wrote
    /// about the same story twice is one outlet, and `source_count` once
    /// counted it as two (d907ae6).
    static func outlets(of story: DigestStory) -> [String] {
        CorroboratedStories.distinctOutlets(of: story)
    }

    /// "Reuters", or "Reuters +3". For an eyebrow, where a list of names
    /// would wrap a single line into three.
    static func compact(_ outlets: [String]) -> String {
        guard let first = outlets.first else { return "" }
        return outlets.count == 1 ? first : "\(first) +\(outlets.count - 1)"
    }

    /// "Reuters, The Verge and 2 more". For a byline, where there is room to
    /// name names but not all of them.
    ///
    /// Up to three named in full, through the same sentence the outlet
    /// section already uses; past three, the first two and a count, because
    /// "A, B, C, D, E and F" under a headline is a list to read rather than
    /// an attribution to glance at.
    static func byline(_ outlets: [String], naming limit: Int = 3) -> String {
        guard outlets.count > limit else {
            return CorroboratedStories.attribution(outlets)
        }
        let named = outlets.prefix(2).joined(separator: ", ")
        return "\(named) and \(outlets.count - 2) more"
    }
}

/// The overlapping marks themselves.
struct SourceStack: View {
    let outlets: [String]
    /// How many marks to draw. Three overlap into a recognisable shape;
    /// a fourth starts to read as a progress indicator.
    var visible: Int = 3
    /// On a photograph, under `LeadCard`'s scrim, where paper-coloured marks
    /// would read as holes punched in the image.
    var onDarkGround: Bool = false

    /// Grows with Dynamic Type, anchored to the caption the marks sit beside.
    ///
    /// Internal rather than private: a private stored property can make the
    /// synthesised memberwise initialiser private with it, and every caller
    /// of this view lives in another file.
    @ScaledMetric(relativeTo: .caption) var diameter: CGFloat = 20

    /// How far each mark tucks under the one before it.
    ///
    /// Earlier marks are drawn on top, so this much of every later mark's
    /// LEFT edge is hidden. At 0.3 the hidden part reached the letters: on
    /// the simulator "AJ" read as "\\J" beside an "F" (ROADMAP task 52).
    private var overlap: CGFloat { diameter * 0.22 }

    var body: some View {
        HStack(spacing: -overlap) {
            ForEach(Array(outlets.prefix(visible).enumerated()), id: \.offset) { index, name in
                mark(name, tucked: index > 0)
                    // Earlier marks sit on top, so the most authoritative
                    // outlet is the one drawn whole.
                    .zIndex(Double(visible - index))
            }
        }
        // The names are said by the label beside the stack. Reading
        // "R, V, FT" as well would be the same outlets twice, badly.
        .accessibilityHidden(true)
    }

    /// `tucked`: this mark sits partly under the one before it, so its
    /// letters are centred in the part that stays visible.
    private func mark(_ name: String, tucked: Bool = false) -> some View {
        Text(SourceAttribution.monogram(for: name))
            .font(.system(size: diameter * 0.4, weight: .bold, design: .rounded))
            // Four bold letters in a 20pt mark need about 0.68; the floor
            // sits below that so CNBC fits at every Dynamic Type size.
            .minimumScaleFactor(0.5)
            .lineLimit(1)
            .foregroundStyle(onDarkGround
                             // Paper ink in BOTH appearances: the mark is
                             // white on a photograph either way, so the
                             // dark-mode ink (near-white) would vanish on it.
                             ? AnyShapeStyle(MercuryColor.ink.light.color)
                             : AnyShapeStyle(MercuryColor.inkMuted.color))
            .padding([.top, .bottom, .trailing], diameter * 0.12)
            // The whole overlap. Half of it was tried (review of #76, to keep
            // a tucked "CNBC" larger) and on the simulator a tucked "BBC"
            // read "3BC" under "AJ". Small letters beat hidden ones.
            .padding(.leading, diameter * 0.12 + (tucked ? overlap : 0))
            .frame(width: diameter, height: diameter)
            .background {
                Circle().fill(onDarkGround
                              ? AnyShapeStyle(.white.opacity(0.92))
                              : AnyShapeStyle(MercuryColor.surfaceRaised.color))
            }
            .overlay {
                Circle().strokeBorder(MercuryColor.hairline.color,
                                      lineWidth: onDarkGround ? 0 : 1)
            }
            // A ring in the ground's own colour, so each mark reads as
            // cut out of the one behind it rather than smudged into it.
            .background {
                Circle()
                    .fill(onDarkGround
                          ? AnyShapeStyle(.black.opacity(0.25))
                          : AnyShapeStyle(MercuryColor.surface.color))
                    .padding(-1.5)
            }
    }
}

/// The stack and its words, as one line under a headline:
/// "Reuters, The Verge and 2 more".
///
/// Only the byline form lives here. The compact eyebrow form ("REUTERS +3")
/// is drawn by `DeskEyebrow`, which threads the waveform and the age between
/// the marks and the words; a second compact view here was used by nothing
/// but its own preview and would have drifted from the one on screen.
struct SourceByline: View {
    let outlets: [String]
    var onDarkGround: Bool = false

    var body: some View {
        if !outlets.isEmpty {
            HStack(spacing: MercurySpace.snug) {
                SourceStack(outlets: outlets, onDarkGround: onDarkGround)
                Text(SourceAttribution.byline(outlets))
                    .font(MercuryType.caption)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

#Preview("Source stacks") {
    VStack(alignment: .leading, spacing: 20) {
        SourceByline(outlets: ["Reuters"])
        SourceByline(outlets: ["the BBC", "Financial Times", "The Verge", "NPR"])
        SourceByline(outlets: ["CNBC", "Al Jazeera"], onDarkGround: true)
            .padding()
            .background(.black)
    }
    .foregroundStyle(MercuryColor.inkFaint.color)
    .padding()
    .background(MercuryColor.surface.color)
}
