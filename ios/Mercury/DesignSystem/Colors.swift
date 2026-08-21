import SwiftUI

/// True black canvas, not dark grey — this is what makes ambient card
/// colours (see `AmbientBackground`) read as vivid. PRD 3.3.
enum MercuryColor {
    static let canvas = Color.black
    static let cardCornerRadius: CGFloat = 28

    // Section-title accents per card family (PRD 3.3 card spec table).
    static let calendarAccent = Color(hue: 0.5, saturation: 0.55, brightness: 0.95)   // cyan
    static let inboxAccent = Color(hue: 0.33, saturation: 0.65, brightness: 0.85)     // bright green
    static let weatherAccent = Color(hue: 0.575, saturation: 0.3, brightness: 1.0)
    static let greetingAccent = Color(hue: 0.09, saturation: 0.4, brightness: 1.0)

    static let textPrimary = Color.white
    static let textSecondary = Color.white.opacity(0.72)
    static let textTertiary = Color.white.opacity(0.45)
}
