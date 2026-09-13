import AppKit
import Foundation
import PDFKit
import Vision

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: pdf_ocr.swift FILE.pdf\n".utf8))
    exit(2)
}

let pdfURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let document = PDFDocument(url: pdfURL) else {
    FileHandle.standardError.write(Data("cannot open PDF\n".utf8))
    exit(1)
}

for pageIndex in 0..<document.pageCount {
    autoreleasepool {
        guard let page = document.page(at: pageIndex) else { return }
        let bounds = page.bounds(for: .mediaBox)
        let scale: CGFloat = 2.0
        let rotated = abs(page.rotation) % 180 == 90
        let targetSize = NSSize(
            width: (rotated ? bounds.height : bounds.width) * scale,
            height: (rotated ? bounds.width : bounds.height) * scale
        )
        let thumbnail = page.thumbnail(of: targetSize, for: .mediaBox)
        var proposedRect = CGRect(origin: .zero, size: targetSize)
        guard let image = thumbnail.cgImage(
            forProposedRect: &proposedRect, context: nil, hints: nil
        ) else { return }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["vi-VN", "en-US"]
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([request])
        } catch {
            FileHandle.standardError.write(Data("OCR page \(pageIndex + 1): \(error)\n".utf8))
            return
        }

        let observations = (request.results ?? []).sorted {
            if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.008 {
                return $0.boundingBox.midY > $1.boundingBox.midY
            }
            return $0.boundingBox.minX < $1.boundingBox.minX
        }
        for observation in observations {
            guard let candidate = observation.topCandidates(1).first else { continue }
            let box = observation.boundingBox
            let record: [String: Any] = [
                "page": pageIndex + 1,
                "text": candidate.string,
                "confidence": candidate.confidence,
                "x": box.minX,
                "y": box.minY,
                "width": box.width,
                "height": box.height,
            ]
            if let data = try? JSONSerialization.data(withJSONObject: record),
               let line = String(data: data, encoding: .utf8) {
                print(line)
            }
        }
    }
}
