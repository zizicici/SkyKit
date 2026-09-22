import CoreMotion

public struct MoonGuidance {
    public let deviceDirection: SIMD3<Double>
    public let angularDistance: Double
    /// Clockwise rotation of an upward arrow in the device's screen plane.
    public let arrowRotation: Double
    public let isBehindCamera: Bool
    public var isAligned: Bool { angularDistance <= 5 }

    public static func calculate(position: MoonPosition, attitude: CMRotationMatrix) -> MoonGuidance {
        let target = position.northWestUp
        // Core Motion rotates reference-frame vectors into device coordinates.
        // Rear camera looks along -Z; device +X is screen right, +Y is screen up.
        let x = attitude.m11 * target.x + attitude.m12 * target.y + attitude.m13 * target.z
        let y = attitude.m21 * target.x + attitude.m22 * target.y + attitude.m23 * target.z
        let z = attitude.m31 * target.x + attitude.m32 * target.y + attitude.m33 * target.z
        let behind = z > 0
        let rotation = behind ? (x < 0 ? -Double.pi / 2 : Double.pi / 2) : atan2(x, y)
        return MoonGuidance(deviceDirection: SIMD3(x, y, z),
                            angularDistance: acos(max(-1, min(1, -z))) * 180 / .pi,
                            arrowRotation: rotation, isBehindCamera: behind)
    }
}
