import SwiftUI

/// Deterministic ambient backgrounds for cards and DeepCast tiles.
///
/// Generating an image per item is slow and expensive. Instead we hash the
/// title into a stable hue and build a mesh gradient from it. Same title
/// always produces the same visual identity, every item looks distinct, and
/// it costs nothing.
struct AmbientBackground: View {

    let seed: String
    var family: Family = .derived

    enum Family {
        case derived        // hue from the seed hash
        case greeting       // warm amber and rust
        case weather        // sky blue
        case calendar       // teal to slate
        case inbox          // olive to forest
    }

    var body: some View {
        ZStack {
            MeshGradient(
                width: 3, height: 3,
                points: Self.meshPoints,
                colors: colors
            )
            NoiseOverlay()
                .blendMode(.overlay)
                .opacity(0.06)
        }
        .drawingGroup()
    }

    // MARK: - Palette

    private var colors: [Color] {
        let (base, secondary, highlight) = palette
        return [
            highlight, base,      secondary,
            base,      secondary, base,
            secondary, base,      highlight,
        ]
    }

    private var palette: (Color, Color, Color) {
        switch family {
        case .greeting: return hues(0.055)   // amber
        case .weather:  return hues(0.575)   // sky
        case .calendar: return hues(0.500)   // teal
        case .inbox:    return hues(0.235)   // olive
        case .derived:  return hues(derivedHue)
        }
    }

    private var derivedHue: Double {
        var hasher = Hasher()
        hasher.combine(seed)
        return Double(abs(hasher.finalize()) % 360) / 360.0
    }

    private func hues(_ h: Double) -> (Color, Color, Color) {
        (
            Color(hue: h, saturation: 0.62, brightness: 0.55),
            Color(hue: wrap(h + 0.08), saturation: 0.70, brightness: 0.38),
            Color(hue: wrap(h - 0.05), saturation: 0.45, brightness: 0.72)
        )
    }

    private func wrap(_ v: Double) -> Double {
        let x = v.truncatingRemainder(dividingBy: 1.0)
        return x < 0 ? x + 1 : x
    }

    private static let meshPoints: [SIMD2<Float>] = [
        [0.0, 0.0], [0.5, 0.0], [1.0, 0.0],
        [0.0, 0.5], [0.6, 0.4], [1.0, 0.5],
        [0.0, 1.0], [0.5, 1.0], [1.0, 1.0],
    ]
}

/// Fine noise texture. Without this, large gradients band visibly on OLED.
private struct NoiseOverlay: View {
    var body: some View {
        Canvas { context, size in
            for _ in 0..<Int(size.width * size.height / 40) {
                let x = Double.random(in: 0...size.width)
                let y = Double.random(in: 0...size.height)
                context.fill(
                    Path(ellipseIn: CGRect(x: x, y: y, width: 1, height: 1)),
                    with: .color(.white.opacity(Double.random(in: 0.1...0.4)))
                )
            }
        }
    }
}
