import SwiftUI

/// The spacing scale.
///
/// There wasn't one. Values were picked per call site, and the same intent got
/// a different number depending on who wrote it: the player alone hardcoded
/// `20` at three separate places for what is one idea — the screen gutter —
/// while cards used `16` and `20` interchangeably for padding that should have
/// matched. Named steps make "same intent, same number" the default rather than
/// something you have to notice.
///
/// A four-point grid, doubling at the top. Anything not on this scale should
/// have a reason at the call site.
enum MercurySpace {

    /// 2 — optical nudges only. If you are using this for layout, use `tight`.
    static let hair: CGFloat = 2
    /// 4 — between a glyph and its label.
    static let tight: CGFloat = 4
    /// 8 — within a tight group: a title and its subtitle.
    static let snug: CGFloat = 8
    /// 12 — between rows in a list.
    static let base: CGFloat = 12
    /// 16 — the default padding inside a card.
    static let comfortable: CGFloat = 16
    /// 20 — the screen gutter. The player's three hardcoded `20`s were all this.
    static let gutter: CGFloat = 20
    /// 24 — between groups inside a card.
    static let generous: CGFloat = 24
    /// 32 — between sections on a page.
    static let section: CGFloat = 32
    /// 48 — above a page's first heading, or between major blocks.
    static let major: CGFloat = 48

    /// Every step, ascending — for tests that check the scale itself.
    static let scale: [CGFloat] = [hair, tight, snug, base, comfortable, gutter, generous, section, major]
}

extension View {
    /// Standard horizontal inset from the screen edge. The player set this
    /// three times with a literal; now it is one call with one name.
    func mercuryGutter() -> some View {
        padding(.horizontal, MercurySpace.gutter)
    }
}
