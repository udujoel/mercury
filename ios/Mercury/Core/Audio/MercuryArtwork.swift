import UIKit

/// The mark, drawn at any size, for the lock screen and Control Center.
///
/// **Why drawn rather than loaded.** The obvious move is to reuse the app
/// icon, and `UIImage(named: "AppIcon")` does sometimes return it — but only
/// sometimes, depending on how the catalog compiled, and a now-playing card
/// that silently has no artwork on some builds is exactly the bug being
/// fixed here. The icon also took three attempts to wire into the catalog
/// correctly (wrong group, wrong target's resources phase, a build setting
/// whose regex matched nothing), each of which built cleanly and shipped
/// nothing. A `UIGraphicsImageRenderer` has no wiring to get wrong.
///
/// The geometry mirrors `scripts/make_app_icon.py` — same sunrise on the
/// same doubled masthead rule — so the lock screen and the home screen show
/// the same mark. If one changes, change both; there is a test that keeps
/// the numbers honest.
enum MercuryArtwork {

    // ADR-012's palette, matching the script's PAPER and ACCENT.
    static let paper = UIColor(red: 0xFB / 255, green: 0xFA / 255,
                               blue: 0xF8 / 255, alpha: 1)
    static let accent = UIColor(red: 0x8E / 255, green: 0x3F / 255,
                                blue: 0x51 / 255, alpha: 1)

    // Fractions of the canvas, so the mark survives any size. Named and
    // valued as in the script.
    static let horizonY: CGFloat = 0.585
    static let sunRadius: CGFloat = 0.235
    static let ruleWeight: CGFloat = 0.030
    static let hairlineWeight: CGFloat = 0.009
    static let hairlineGap: CGFloat = 0.026
    /// Margin, not a brim. The first cut of the icon inset the rule far
    /// enough that it read as the brim of a bowler hat rather than as a
    /// horizon — a horizon has to run to the edge of what you can see.
    static let ruleInset: CGFloat = 0.055

    /// One image per size, because the lock screen asks repeatedly.
    ///
    /// `NSCache` rather than a dictionary: this holds bitmaps, and the one
    /// thing that must not happen is the artwork keeping memory alive while
    /// audio decoding wants it.
    private static let cache = NSCache<NSNumber, UIImage>()

    static func image(side: CGFloat) -> UIImage {
        let key = NSNumber(value: Double(side))
        if let cached = cache.object(forKey: key) { return cached }

        let format = UIGraphicsImageRendererFormat.default()
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(
            size: CGSize(width: side, height: side), format: format)

        let image = renderer.image { context in
            let cg = context.cgContext
            paper.setFill()
            cg.fill(CGRect(x: 0, y: 0, width: side, height: side))
            accent.setFill()

            // The sun: a half-disc resting ON the horizon. A full circle
            // floating above it is a dot; the horizon is what makes it a
            // sunrise.
            let horizon = horizonY * side
            let radius = sunRadius * side
            let sun = CGRect(x: side / 2 - radius, y: horizon - radius,
                             width: radius * 2, height: radius * 2)
            cg.saveGState()
            cg.clip(to: CGRect(x: 0, y: 0, width: side, height: horizon))
            cg.fillEllipse(in: sun)
            cg.restoreGState()

            // The masthead device: a heavy rule with a hairline under it.
            // Two weights with air between them; a single rule is just a
            // line.
            let inset = ruleInset * side
            let width = side - inset * 2
            cg.fill(CGRect(x: inset, y: horizon,
                           width: width, height: ruleWeight * side))
            cg.fill(CGRect(x: inset,
                           y: horizon + (ruleWeight + hairlineGap) * side,
                           width: width, height: hairlineWeight * side))
        }

        cache.setObject(image, forKey: key)
        return image
    }
}
