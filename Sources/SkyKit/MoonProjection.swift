import CoreGraphics
import Foundation

/// Pinhole projection for a portrait rear-camera preview.
public struct MoonProjection {
    public let point: CGPoint
    public let isOnScreen: Bool
    public let arrowRotation: Double

    public static func project(_ guidance: MoonGuidance, imageRect: CGRect,
                               horizontalFieldOfView: Double, zoom: Double,
                               markerBounds: CGRect) -> MoonProjection? {
        guard horizontalFieldOfView > 0, horizontalFieldOfView < 180,
              zoom.isFinite, zoom >= 1, imageRect.width > 0, imageRect.height > 0,
              markerBounds.width > 0, markerBounds.height > 0 else { return nil }
        // Apple's FOV is horizontal in the unrotated capture format. That axis
        // becomes vertical in a portrait preview. imageRect includes aspect-fit
        // letterboxing or aspect-fill cropping as reported by the preview layer.
        let focalLength = imageRect.height * zoom / (2 * tan(horizontalFieldOfView * .pi / 360))
        let center = CGPoint(x: imageRect.midX, y: imageRect.midY)
        let direction = guidance.deviceDirection
        let depth = -direction.z
        if depth > 0.0001 {
            let point = CGPoint(x: center.x + focalLength * direction.x / depth,
                                y: center.y - focalLength * direction.y / depth)
            if markerBounds.contains(point) {
                return MoonProjection(point: point, isOnScreen: true, arrowRotation: guidance.arrowRotation)
            }
        }
        // Clamp an arrow to the safe perimeter, never mirror a target behind
        // the camera through the projection plane.
        let origin = CGPoint(x: markerBounds.midX, y: markerBounds.midY)
        let dx = sin(guidance.arrowRotation)
        let dy = -cos(guidance.arrowRotation)
        let scaleX = abs(dx) > 0.0001 ? markerBounds.width / 2 / abs(dx) : .infinity
        let scaleY = abs(dy) > 0.0001 ? markerBounds.height / 2 / abs(dy) : .infinity
        let scale = min(scaleX, scaleY)
        return MoonProjection(point: CGPoint(x: origin.x + dx * scale, y: origin.y + dy * scale),
                              isOnScreen: false, arrowRotation: guidance.arrowRotation)
    }
}
