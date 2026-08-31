import SwiftUI

/// A wash of the day's colour across a surface.
///
/// This used to be the loudest thing on screen: four saturated mesh gradients,
/// one per card, each a different hue. ADR-012 moved the register to editorial,
/// where the paper stays paper and the colour is carried by ink. So the same
/// component survives at a fraction of the volume — a tint you notice only if
/// you look for it, which is what stops a page of white cards feeling like a
/// settings screen without turning it into a lava lamp.
///
/// Two bugs were fixed on the way through, both in `NoiseOverlay`.
struct AmbientBackground: View {

    let seed: String
    /// Multiplies the tint. 1.0 is a full-strength page wash; cards want less.
    var intensity: Double = 1.0
    var date: Date = Date()
    var family: Family = .derived

    /// Superseded by the one-edition-one-colour rule, kept so `Features/` keeps
    /// building until tasks 2 and 3 migrate.
    ///
    /// These used to be four independent hues — amber, sky, teal, olive — which
    /// is what made the home screen read as four unrelated apps stacked up.
    /// They now resolve through the day's anchor like everything else: each
    /// case takes a fixed position in the day's ±0.09 band, evenly spaced so
    /// the four are as distinguishable as the band allows while staying
    /// obviously the same morning. Evenly spaced rather than hashed, because
    /// four hashed seeds cluster at random and two of the four usually land on
    /// top of each other.
    enum Family {
        case derived
        case greeting
        case weather
        case calendar
        case inbox

        /// Position within the band, in `[-1, 1]`.
        var bandPosition: Double? {
            switch self {
            case .derived:  return nil   // hashed from the seed instead
            case .weather:  return -1.0
            case .calendar: return -1.0 / 3.0
            case .inbox:    return  1.0 / 3.0
            case .greeting: return  1.0
            }
        }
    }

    /// The strongest the tint is ever allowed to get.
    ///
    /// It is a token rather than a literal because `ContrastTests` asserts that
    /// every ink token still clears AA over a ground washed this hard. A tint
    /// looks free and is not: it moves the ground's luminance, and the ink was
    /// measured against the unwashed value.
    static let maxTintOpacity = 0.10

    private var hueOffset: Double {
        if let position = family.bandPosition { return position * DawnPalette.hueBand }
        return DawnPalette.hueOffset(seed: seed)
    }

    private var accent: RGB {
        DawnPalette.accentColor(hue: DawnPalette.anchor(for: date).hue + hueOffset, dark: false)
    }

    private var tint: Double { Self.maxTintOpacity * min(max(intensity, 0), 1) }

    var body: some View {
        ZStack {
            MercuryColor.surface.color

            if #available(iOS 18.0, *) {
                MeshGradient(width: 3, height: 3, points: Self.meshPoints, colors: meshColors)
                    .opacity(tint)
            } else {
                fallbackWash.opacity(tint)
            }

            // Dither, not texture. Large near-flat gradients band visibly on
            // OLED; a little noise breaks the bands up.
            NoiseOverlay(seed: seed)
                .blendMode(.softLight)
                .opacity(0.035)
        }
        .drawingGroup()
    }

    /// The wash is one hue at three strengths rather than three hues — the
    /// edition has one colour, and a multi-hue mesh is what made the old cards
    /// read as four unrelated apps.
    private var meshColors: [Color] {
        let c = accent.color
        let strong = c, mid = c.opacity(0.55), soft = c.opacity(0.15)
        return [strong, mid, soft,
                mid, soft, mid,
                soft, mid, strong]
    }

    /// iOS 17 path — `MeshGradient` is iOS 18+. Layered gradients reproduce the
    /// same corner-weighted wash. Kept working deliberately: the project
    /// targets iOS 17, and this fallback was itself a fix in the MVP pass.
    private var fallbackWash: some View {
        let c = accent.color
        return ZStack {
            LinearGradient(colors: [c, c.opacity(0.2), c.opacity(0.65)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            RadialGradient(colors: [c.opacity(0.7), .clear],
                           center: .topLeading, startRadius: 0, endRadius: 420)
            RadialGradient(colors: [c.opacity(0.5), .clear],
                           center: .bottomTrailing, startRadius: 0, endRadius: 420)
        }
    }

    private static let meshPoints: [SIMD2<Float>] = [
        [0.0, 0.0], [0.5, 0.0], [1.0, 0.0],
        [0.0, 0.5], [0.6, 0.4], [1.0, 0.5],
        [0.0, 1.0], [0.5, 1.0], [1.0, 1.0],
    ]
}

/// Fixed dither field.
///
/// The version this replaces had two problems. It called `Double.random` inside
/// a `Canvas` draw closure, so the field was re-rolled on every redraw — the
/// texture crawled during any animation, and "deterministic ambient background"
/// was false twice over. And it drew `width * height / 40` one-point ellipses:
/// about 5,850 individual `context.fill` calls for a single full-width card,
/// scaling with area, every frame.
///
/// This draws from a table generated once by a seeded PRNG, batched into three
/// alpha buckets so a redraw is three path fills instead of thousands. Cost is
/// constant in the surface's area.
struct NoiseOverlay: View {
    var seed: String = ""

    /// Enough points to break banding at card and full-screen sizes; small
    /// enough that building the paths is not worth caching.
    static let pointCount = 900

    private static let buckets = 3

    /// Unit-square points, split by alpha bucket. Generated once per process
    /// from a fixed seed — `static let` is lazy and `let`, so this is
    /// initialised exactly once and is safe to touch from any thread.
    private static let field: [[CGPoint]] = {
        var generator = SplitMix64(seed: 0x9E37_79B9_7F4A_7C15)
        var out = [[CGPoint]](repeating: [], count: buckets)
        for _ in 0..<pointCount {
            let x = generator.nextUnit()
            let y = generator.nextUnit()
            let bucket = Int(generator.nextUnit() * Double(buckets)) % buckets
            out[bucket].append(CGPoint(x: x, y: y))
        }
        return out
    }()

    /// Per-instance offset so two surfaces on screen do not share a field and
    /// reveal the pattern. Deterministic in the seed, like everything else.
    private var offset: CGPoint {
        let h = DawnPalette.fnv1a(seed)
        return CGPoint(x: Double(h & 0xFFFF) / 65_536.0,
                       y: Double((h >> 16) & 0xFFFF) / 65_536.0)
    }

    var body: some View {
        Canvas { context, size in
            let shift = offset
            for (index, points) in Self.field.enumerated() {
                var path = Path()
                for point in points {
                    let x = (point.x + shift.x).truncatingRemainder(dividingBy: 1) * size.width
                    let y = (point.y + shift.y).truncatingRemainder(dividingBy: 1) * size.height
                    path.addRect(CGRect(x: x, y: y, width: 1, height: 1))
                }
                let alpha = 0.18 + 0.22 * Double(index) / Double(Self.buckets - 1)
                context.fill(path, with: .color(.white.opacity(alpha)))
            }
        }
        .allowsHitTesting(false)
    }
}

/// SplitMix64. Small, fast, and — the only property that matters here — a pure
/// function of its seed, so the dither field is identical in every process.
struct SplitMix64: RandomNumberGenerator {
    private var state: UInt64

    init(seed: UInt64) { state = seed }

    mutating func next() -> UInt64 {
        state = state &+ 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }

    /// A `Double` in `[0, 1)`. Takes the top 53 bits so the division is exact.
    mutating func nextUnit() -> Double {
        Double(next() >> 11) * (1.0 / 9_007_199_254_740_992.0)
    }
}
