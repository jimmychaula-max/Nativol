import AppKit

guard CommandLine.arguments.count == 2 else { exit(2) }
let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

func drawIcon(size: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                  isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    let scale = CGFloat(size) / 1024
    context.cgContext.scaleBy(x: scale, y: scale)
    let shape = NSBezierPath(roundedRect: NSRect(x: 82, y: 82, width: 860, height: 860), xRadius: 190, yRadius: 190)
    let gradient = NSGradient(starting: NSColor(calibratedRed: 0.12, green: 0.52, blue: 0.48, alpha: 1),
                              ending: NSColor(calibratedRed: 0.05, green: 0.29, blue: 0.29, alpha: 1))!
    gradient.draw(in: shape, angle: -70)
    NSColor.white.setStroke()
    let mark = NSBezierPath()
    mark.lineWidth = 82
    mark.lineCapStyle = .round
    mark.lineJoinStyle = .round
    mark.move(to: NSPoint(x: 330, y: 310))
    mark.line(to: NSPoint(x: 330, y: 710))
    mark.line(to: NSPoint(x: 694, y: 310))
    mark.line(to: NSPoint(x: 694, y: 710))
    mark.stroke()
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}

for point in [16, 32, 128, 256, 512] {
    for factor in [1, 2] {
        let suffix = factor == 2 ? "@2x" : ""
        let url = folder.appendingPathComponent("icon_\(point)x\(point)\(suffix).png")
        try drawIcon(size: point * factor).write(to: url)
    }
}
