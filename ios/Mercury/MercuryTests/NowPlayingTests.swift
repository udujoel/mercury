import XCTest
@testable import Mercury

/// The lock screen card: an image, and a title that follows the chapter.
///
/// Neither existed. `NowPlayingService` set no artwork at all and a single
/// hardcoded "Your briefing", so a briefing that moves through seven
/// subjects in eight minutes showed one unchanging line and a blank square.
final class NowPlayingTests: XCTestCase {

    /// The artwork has to be a DRAWING, not an empty canvas.
    ///
    /// The failure this guards against is the one the app icon had three
    /// times: everything wired up, everything building, and nothing on
    /// screen. A blank paper-coloured square would satisfy "an image
    /// exists" and satisfy nobody looking at their lock screen, so this
    /// asserts the accent is actually on it.
    func testTheArtworkIsDrawnAndNotBlank() throws {
        let side: CGFloat = 120
        let image = MercuryArtwork.image(side: side)

        XCTAssertEqual(image.size.width, side)
        XCTAssertEqual(image.size.height, side)

        let cg = try XCTUnwrap(image.cgImage)
        let width = cg.width, height = cg.height
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let context = try XCTUnwrap(CGContext(
            data: &pixels, width: width, height: height,
            bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        context.draw(cg, in: CGRect(x: 0, y: 0, width: width, height: height))

        /// The sun's centre, and a corner that must stay paper.
        func pixel(atX x: Int, y: Int) -> (UInt8, UInt8, UInt8) {
            let i = (y * width + x) * 4
            return (pixels[i], pixels[i + 1], pixels[i + 2])
        }

        // Just above the horizon, dead centre: inside the half-disc.
        let sun = pixel(atX: width / 2,
                        y: Int(CGFloat(height) * (MercuryArtwork.horizonY - 0.1)))
        XCTAssertLessThan(sun.0, 200, "the sun should be accent, not paper")
        XCTAssertLessThan(sun.1, 150)

        // Top-left corner: sky, and the one place nothing is ever drawn.
        let corner = pixel(atX: 2, y: 2)
        XCTAssertGreaterThan(corner.0, 240, "the ground should stay paper")
        XCTAssertGreaterThan(corner.1, 240)
    }

    /// Two sizes, because the lock screen, Control Center and CarPlay each
    /// ask for their own and a cache keyed wrongly would serve one to all.
    func testEachSizeGetsItsOwnImage() {
        XCTAssertEqual(MercuryArtwork.image(side: 64).size.width, 64)
        XCTAssertEqual(MercuryArtwork.image(side: 256).size.width, 256)
    }

    /// The card names the briefing's OWN day, not today's.
    ///
    /// A restored briefing is yesterday's, and a lock screen calling it
    /// today's is wrong somewhere the user cannot correct it. Asserted by
    /// comparing two days rather than by pinning the word "Saturday" —
    /// three tests in this project have already been broken by hardcoding
    /// something the locale or timezone owns.
    func testTheNameFollowsTheBriefingsOwnDay() {
        let saturday = ManifestFixtures.made(at: "2026-09-05T05:00:00Z")
        let sunday = ManifestFixtures.made(at: "2026-09-06T05:00:00Z")

        let a = PlaybackCoordinator.briefingName(for: saturday)
        let b = PlaybackCoordinator.briefingName(for: sunday)

        XCTAssertTrue(a.hasSuffix("'s briefing"), a)
        XCTAssertNotEqual(a, b, "consecutive days must not share a name")
        XCTAssertFalse(a.hasPrefix("'s"), "the weekday must not be empty")
    }
}
