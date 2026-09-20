import SwiftUI
import simd

/// A panel's four corners on screen (points), clockwise from top-left.
struct Quad: Equatable {
    var tl: CGPoint, tr: CGPoint, br: CGPoint, bl: CGPoint

    init(tl: CGPoint, tr: CGPoint, br: CGPoint, bl: CGPoint) { self.tl = tl; self.tr = tr; self.br = br; self.bl = bl }
    init(rect r: CGRect) {
        self.init(tl: CGPoint(x: r.minX, y: r.minY), tr: CGPoint(x: r.maxX, y: r.minY),
                  br: CGPoint(x: r.maxX, y: r.maxY), bl: CGPoint(x: r.minX, y: r.maxY))
    }

    var leftMid: CGPoint { mid(tl, bl) }
    var rightMid: CGPoint { mid(tr, br) }
    var topMid: CGPoint { mid(tl, tr) }
    var center: CGPoint { mid(mid(tl, br), mid(tr, bl)) }
}

func mid(_ a: CGPoint, _ b: CGPoint) -> CGPoint { CGPoint(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2) }

/// Perspective transform taking a view of `size` (origin top-left) onto `quad`, so a flat SwiftUI panel
/// renders as a plane in 3D.
enum Homography {
    /// Row-major 3×3 mapping (x, y, 1) in the panel to screen.
    static func matrix(from size: CGSize, to q: Quad) -> [Double]? {
        let src = [(0.0, 0.0), (Double(size.width), 0.0), (Double(size.width), Double(size.height)), (0.0, Double(size.height))]
        let dst = [q.tl, q.tr, q.br, q.bl].map { (Double($0.x), Double($0.y)) }
        var A: [[Double]] = [], b: [Double] = []
        for i in 0..<4 {
            let (x, y) = src[i], (u, v) = dst[i]
            A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
            A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
        }
        guard let h = solve(A, b) else { return nil }
        return h + [1]
    }

    static func projection(from size: CGSize, to q: Quad) -> ProjectionTransform {
        guard let h = matrix(from: size, to: q) else { return ProjectionTransform() }
        var p = ProjectionTransform()
        p.m11 = CGFloat(h[0]); p.m21 = CGFloat(h[1]); p.m31 = CGFloat(h[2])
        p.m12 = CGFloat(h[3]); p.m22 = CGFloat(h[4]); p.m32 = CGFloat(h[5])
        p.m13 = CGFloat(h[6]); p.m23 = CGFloat(h[7]); p.m33 = CGFloat(h[8])
        return p
    }

    /// Panel-local point → screen point.
    static func apply(_ p: CGPoint, size: CGSize, quad: Quad) -> CGPoint? {
        guard let h = matrix(from: size, to: quad) else { return nil }
        let x = Double(p.x), y = Double(p.y), w = h[6] * x + h[7] * y + h[8]
        return CGPoint(x: (h[0] * x + h[1] * y + h[2]) / w, y: (h[3] * x + h[4] * y + h[5]) / w)
    }

    /// Screen point → panel-local point (nil if the panel is degenerate).
    static func unproject(_ p: CGPoint, size: CGSize, quad: Quad) -> CGPoint? {
        guard let h = matrix(from: size, to: quad) else { return nil }
        let (a, b, c, d, e, f, g, hh, i) = (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], h[8])
        // Inverse of a 3×3
        let A = e * i - f * hh, B = -(d * i - f * g), C = d * hh - e * g
        let det = a * A + b * B + c * C
        guard abs(det) > 1e-12 else { return nil }
        let inv = [A, -(b * i - c * hh), b * f - c * e,
                   B, a * i - c * g, -(a * f - c * d),
                   C, -(a * hh - b * g), a * e - b * d].map { $0 / det }
        let x = Double(p.x), y = Double(p.y)
        let w = inv[6] * x + inv[7] * y + inv[8]
        return CGPoint(x: (inv[0] * x + inv[1] * y + inv[2]) / w, y: (inv[3] * x + inv[4] * y + inv[5]) / w)
    }

    private static func solve(_ A: [[Double]], _ b: [Double]) -> [Double]? {
        let n = b.count
        var M = A.enumerated().map { $0.element + [b[$0.offset]] }
        for c in 0..<n {
            guard let pivot = (c..<n).max(by: { abs(M[$0][c]) < abs(M[$1][c]) }), abs(M[pivot][c]) > 1e-12 else { return nil }
            M.swapAt(c, pivot)
            for r in 0..<n where r != c {
                let f = M[r][c] / M[c][c]
                for k in c...n { M[r][k] -= f * M[c][k] }
            }
        }
        return (0..<n).map { M[$0][n] / M[$0][$0] }
    }
}

/// Camera access the scene needs: rays through screen points and projection of world points.
protocol SceneProjector {
    func ray(through point: CGPoint) -> (origin: SIMD3<Float>, direction: SIMD3<Float>)?
    func project(_ world: SIMD3<Float>) -> CGPoint?
    var cameraPosition: SIMD3<Float> { get }
}
