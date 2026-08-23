#!/usr/bin/env swift

import Foundation
import CoreGraphics
import ImageIO
import Vision

enum LocalOCRError: Error, CustomStringConvertible {
    case usage
    case unreadableImage(String)
    case recognitionFailed(String)

    var description: String {
        switch self {
        case .usage:
            return "usage: local_ocr.swift <image-path>"
        case .unreadableImage(let path):
            return "unable to decode image: \(path)"
        case .recognitionFailed(let message):
            return "text recognition failed: \(message)"
        }
    }
}

func recognizeText(at path: String) throws -> String {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let source = CGImageSourceCreateWithURL(url, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw LocalOCRError.unreadableImage(path)
    }

    var observations: [VNRecognizedTextObservation] = []
    var requestError: Error?
    let request = VNRecognizeTextRequest { request, error in
        requestError = error
        observations = request.results as? [VNRecognizedTextObservation] ?? []
    }
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["ja-JP", "en-US"]

    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
    if let error = requestError {
        throw LocalOCRError.recognitionFailed(error.localizedDescription)
    }

    let ordered = observations.sorted { lhs, rhs in
        let verticalDelta = lhs.boundingBox.midY - rhs.boundingBox.midY
        if abs(verticalDelta) > 0.015 {
            return verticalDelta > 0
        }
        return lhs.boundingBox.minX < rhs.boundingBox.minX
    }
    return ordered.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}

do {
    guard CommandLine.arguments.count == 2 else {
        throw LocalOCRError.usage
    }
    let text = try recognizeText(at: CommandLine.arguments[1])
    FileHandle.standardOutput.write(Data(text.utf8))
    if !text.isEmpty {
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
} catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8))
    exit(1)
}
