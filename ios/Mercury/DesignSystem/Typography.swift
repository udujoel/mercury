import SwiftUI

/// One rule, and every screen follows it:
///
/// **Anything a host says is set in the serif. Anything the app says is set in
/// SF.** The greeting and the transcript are speech. Chapter tabs, timecodes,
/// labels and counts are the app talking. Two voices, two typefaces, and a
/// reader can tell which is which without being told.
///
/// Every token below uses a *text-style* overload — `.system(.title2, …)` —
/// rather than a point size. Before ADR-012 every font in the app was a fixed
/// size, which meant Dynamic Type did nothing at all: an app whose entire
/// purpose is reading things aloud to you shipped type that would not grow for
/// someone who needs it larger. Text styles scale; point sizes do not.
///
/// The trade this makes: `greeting` was `.system(size: 40)` and is now
/// `.largeTitle`, which is 34pt at the default Dynamic Type size. It is
/// smaller by default and larger than 40 by the third accessibility step. That
/// is the right way round.
enum MercuryType {

    // MARK: - Voice — what the hosts say

    /// The morning greeting. The one piece of type that is allowed to be big.
    static let greeting = Font.system(.largeTitle, design: .serif, weight: .light)

    /// Player Zone 1: the sentence currently being spoken.
    ///
    /// Serif, because it is speech. PRD 3.4 specified 26pt medium sans, centred;
    /// ADR-012 keeps the intent — the sentence is the composition — and changes
    /// the execution, because on paper a serif line set ragged-right against a
    /// wide margin composes the way a printed pull-quote does.
    static let transcript = Font.system(.title2, design: .serif, weight: .regular)

    /// A quoted line inside show notes or a card — host speech at body size.
    static let quote = Font.system(.body, design: .serif, weight: .regular)

    // MARK: - Interface — what the app says

    /// A story's headline, on the detail page and the desk's lead card.
    ///
    /// Bold SF, not the serif. Both places used to borrow `greeting`, which
    /// broke the one rule this file exists for: a headline is a publication
    /// talking, not a host, and setting it in the hosts' face made every
    /// story on the desk read like something said aloud. Particle sets its
    /// headlines heavy and sans for the same reason — the weight is what
    /// tells you this is the thing to read first.
    static let headline = Font.system(.title2, design: .default, weight: .bold)

    /// Long-form reading on a story page — the summary points and why it
    /// matters. One step up from `cardBody`, because this is the screen
    /// whose whole job is to be read rather than scanned.
    static let reading = Font.system(.body, design: .default, weight: .regular)

    /// Section titles on cards.
    static let cardTitle = Font.system(.title3, design: .default, weight: .semibold)

    /// The standard reading size for app-authored text.
    static let cardBody = Font.system(.subheadline, design: .default, weight: .regular)

    /// Chapter tabs, buttons, anything that is a control's label.
    static let chapterTab = Font.system(.footnote, design: .default, weight: .semibold)

    /// Bylines, source names, the quiet line under a heading.
    static let caption = Font.system(.caption, design: .default, weight: .regular)

    /// A small all-caps label above a group — "YOUR DAY". Pair with
    /// `.textCase(.uppercase)` and `.tracking(0.6)` at the call site.
    static let eyebrow = Font.system(.caption2, design: .default, weight: .semibold)

    // MARK: - Numerals that have to line up

    /// Timecodes, durations, counts — anything where digits sit in a column or
    /// tick upward. Proportional digits jitter as the numbers change, which is
    /// distracting next to a progress bar and wrong in a table.
    static let timecode = Font.system(.footnote, design: .default, weight: .regular).monospacedDigit()

    /// Larger tabular figures — a temperature, a duration on the player.
    static let figure = Font.system(.title3, design: .default, weight: .medium).monospacedDigit()

    /// Tabular caption, for counts beside a label.
    static let figureCaption = Font.system(.caption, design: .default, weight: .regular).monospacedDigit()

    // MARK: - Line spacing

    /// Speech is set looser than interface text; a long serif measure needs the
    /// leading or it sets like a paragraph of terms and conditions.
    static let voiceLineSpacing: CGFloat = 8
    static let interfaceLineSpacing: CGFloat = 2
}

extension MercuryType {
    /// What each token is *supposed* to be, as data.
    ///
    /// `Font` is opaque — there is no supported way to ask one for its point
    /// size or text style — so the only sound way to prove a token scales is to
    /// rebuild it from a text style and check the two are equal. `Font` is
    /// `Hashable`, and a fixed `.system(size:)` font is never equal to a
    /// text-style one, which is exactly the regression worth catching.
    static let dynamicTypeContract: [(name: String, font: Font, style: Font.TextStyle,
                                      design: Font.Design, weight: Font.Weight, tabular: Bool)] = [
        ("greeting",      greeting,      .largeTitle,  .serif,   .light,    false),
        ("transcript",    transcript,    .title2,      .serif,   .regular,  false),
        ("quote",         quote,         .body,        .serif,   .regular,  false),
        ("headline",      headline,      .title2,      .default, .bold,     false),
        ("reading",       reading,       .body,        .default, .regular,  false),
        ("cardTitle",     cardTitle,     .title3,      .default, .semibold, false),
        ("cardBody",      cardBody,      .subheadline, .default, .regular,  false),
        ("chapterTab",    chapterTab,    .footnote,    .default, .semibold, false),
        ("caption",       caption,       .caption,     .default, .regular,  false),
        ("eyebrow",       eyebrow,       .caption2,    .default, .semibold, false),
        ("timecode",      timecode,      .footnote,    .default, .regular,  true),
        ("figure",        figure,        .title3,      .default, .medium,   true),
        ("figureCaption", figureCaption, .caption,     .default, .regular,  true),
    ]

    /// Tokens whose voice is the hosts', not the app's. The serif/sans split is
    /// the one rule every other screen has to follow, so it is worth a test.
    static let voiceTokenNames: Set<String> = ["greeting", "transcript", "quote"]
}
