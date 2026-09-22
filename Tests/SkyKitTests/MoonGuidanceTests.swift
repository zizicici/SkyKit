import XCTest
import CoreMotion
import CoreGraphics
// Deliberately not @testable — see MoonPositionTests.
import SkyKit

final class MoonGuidanceTests: XCTestCase {
    // Upright portrait device, rear camera pointing to true north at the horizon.
    private let facingNorth = CMRotationMatrix(m11: 0, m12: -1, m13: 0,
                                               m21: 0, m22: 0, m23: 1,
                                               m31: -1, m32: 0, m33: 0)

    func testCameraAxisAlignmentAndTargetBehindCamera() {
        let forward = MoonGuidance.calculate(position: MoonPosition(azimuth: 0, altitude: 0), attitude: facingNorth)
        XCTAssertTrue(forward.isAligned)
        XCTAssertFalse(forward.isBehindCamera)
        XCTAssertEqual(forward.angularDistance, 0, accuracy: 0.00001)
        let behind = MoonGuidance.calculate(position: MoonPosition(azimuth: 180, altitude: 0), attitude: facingNorth)
        XCTAssertTrue(behind.isBehindCamera)
        XCTAssertFalse(behind.isAligned)
        XCTAssertEqual(behind.angularDistance, 180, accuracy: 0.00001)
        XCTAssertTrue(behind.arrowRotation.isFinite)
    }

    func testArrowPointsRightLeftUpAndDown() {
        for (azimuth, altitude, rotation) in [(30.0, 0.0, Double.pi / 2), (330, 0, -Double.pi / 2),
                                              (0, 30, 0), (0, -30, Double.pi)] {
            let guidance = MoonGuidance.calculate(position: MoonPosition(azimuth: azimuth, altitude: altitude),
                                                  attitude: facingNorth)
            XCTAssertEqual(abs(guidance.arrowRotation - rotation), 0, accuracy: 0.00001)
            XCTAssertEqual(guidance.angularDistance, 30, accuracy: 0.00001)
        }
    }

    func testPhoneRollRotatesArrowInScreenCoordinates() {
        let rolledClockwise = CMRotationMatrix(m11: 0, m12: 0, m13: -1,
                                               m21: 0, m22: -1, m23: 0,
                                               m31: -1, m32: 0, m33: 0)
        let guidance = MoonGuidance.calculate(position: MoonPosition(azimuth: 30, altitude: 0), attitude: rolledClockwise)
        XCTAssertEqual(guidance.arrowRotation, 0, accuracy: 0.00001)
        XCTAssertEqual(guidance.angularDistance, 30, accuracy: 0.00001)
    }

    func testNorthWrapAndZenith() {
        for azimuth in [359.0, 1] {
            let guidance = MoonGuidance.calculate(position: MoonPosition(azimuth: azimuth, altitude: 0), attitude: facingNorth)
            XCTAssertTrue(guidance.isAligned)
            XCTAssertEqual(guidance.angularDistance, 1, accuracy: 0.00001)
        }
        let cameraUp = CMRotationMatrix(m11: 1, m12: 0, m13: 0,
                                        m21: 0, m22: -1, m23: 0,
                                        m31: 0, m32: 0, m33: -1)
        let guidance = MoonGuidance.calculate(position: MoonPosition(azimuth: 180, altitude: 90), attitude: cameraUp)
        XCTAssertTrue(guidance.isAligned)
        XCTAssertTrue(guidance.arrowRotation.isFinite)
    }

    func testPerspectiveProjectionRespondsToTurningAndZoom() throws {
        let image = CGRect(x: 0, y: 0, width: 300, height: 400)
        func projection(azimuth: Double, altitude: Double = 0, zoom: Double = 1) throws -> MoonProjection {
            try XCTUnwrap(MoonProjection.project(
                .calculate(position: MoonPosition(azimuth: azimuth, altitude: altitude), attitude: facingNorth),
                imageRect: image, horizontalFieldOfView: 90, zoom: zoom, markerBounds: image.insetBy(dx: 24, dy: 24)))
        }
        let right = try projection(azimuth: 20)
        XCTAssertTrue(right.isOnScreen)
        XCTAssertEqual(right.point.x, 150 + 200 * tan(20 * .pi / 180), accuracy: 0.001)
        XCTAssertEqual(right.point.y, 200, accuracy: 0.001)
        XCTAssertLessThan(try projection(azimuth: 10).point.x, right.point.x)
        XCTAssertEqual(try projection(azimuth: 0).point.x, 150, accuracy: 0.001)
        XCTAssertLessThan(try projection(azimuth: 0, altitude: 20).point.y, 200)
        let zoomed = try projection(azimuth: 20, zoom: 2)
        XCTAssertFalse(zoomed.isOnScreen)
        XCTAssertEqual(zoomed.point.x, 276, accuracy: 0.001)
        XCTAssertEqual(zoomed.arrowRotation, .pi / 2, accuracy: 0.001)
    }

    func testProjectionLetterboxingBehindCameraAndInvalidGeometry() throws {
        let image = CGRect(x: 0, y: 100, width: 300, height: 400)
        let forward = MoonGuidance.calculate(position: MoonPosition(azimuth: 0, altitude: 0), attitude: facingNorth)
        let centered = try XCTUnwrap(MoonProjection.project(forward, imageRect: image,
            horizontalFieldOfView: 60, zoom: 1, markerBounds: image))
        XCTAssertEqual(centered.point, CGPoint(x: 150, y: 300))
        for azimuth in [90.0, 180, 270] {
            let guidance = MoonGuidance.calculate(position: MoonPosition(azimuth: azimuth, altitude: 0), attitude: facingNorth)
            let result = try XCTUnwrap(MoonProjection.project(guidance, imageRect: image,
                horizontalFieldOfView: 60, zoom: 1, markerBounds: image.insetBy(dx: 24, dy: 24)))
            XCTAssertFalse(result.isOnScreen)
            XCTAssertTrue(result.point.x.isFinite && result.point.y.isFinite)
            XCTAssertEqual(result.point.x, azimuth == 270 ? 24 : 276, accuracy: 0.001)
        }
        XCTAssertNil(MoonProjection.project(forward, imageRect: image, horizontalFieldOfView: 0, zoom: 1, markerBounds: image))
        XCTAssertNil(MoonProjection.project(forward, imageRect: image, horizontalFieldOfView: 60, zoom: 1, markerBounds: .zero))
    }
}
