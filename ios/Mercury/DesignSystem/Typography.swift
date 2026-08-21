import SwiftUI

/// Type scale. The player's Zone 1 sentence (~26pt medium, centred, generous
/// line height) is the anchor size per PRD 3.4.
enum MercuryType {
    static let greeting = Font.system(size: 40, weight: .light, design: .serif)
    static let cardTitle = Font.system(size: 20, weight: .semibold)
    static let cardBody = Font.system(size: 15, weight: .regular)
    static let transcript = Font.system(size: 26, weight: .medium, design: .default)
    static let chapterTab = Font.system(size: 14, weight: .semibold)
    static let caption = Font.system(size: 12, weight: .regular)
}
