#!/usr/bin/env swift

import CoreGraphics
import CryptoKit
import Foundation
import ImageIO

struct Options {
    let inputDirectory: URL
    let outputDirectory: URL
    let maxDimension: Int
    let quality: Double
}

struct PhotoRecord: Encodable {
    let id: String
    let object_key: String
}

struct PhotoManifest: Encodable {
    let photos: [PhotoRecord]
}

enum ProcessingError: Error, CustomStringConvertible {
    case usage
    case invalidOptions
    case invalidInputDirectory
    case unsafeOutputDirectory
    case outputDirectoryNotEmpty
    case noImagesFound
    case imageDecodeFailed
    case imageEncodeFailed
    case imageMetadataVerificationFailed
    case outputWriteFailed
    case manifestWriteFailed

    var description: String {
        switch self {
        case .usage:
            return "Usage: process_photos.swift --input DIRECTORY --output DIRECTORY [--max-dimension PIXELS] [--quality 0...1]"
        case .invalidOptions:
            return "Image processing options are invalid"
        case .invalidInputDirectory:
            return "Input directory does not exist or is not a directory"
        case .unsafeOutputDirectory:
            return "Output directory must be separate from the input directory"
        case .outputDirectoryNotEmpty:
            return "Output directory must be empty; existing files were left untouched"
        case .noImagesFound:
            return "No supported images were found in the input directory"
        case .imageDecodeFailed:
            return "A supported image could not be decoded"
        case .imageEncodeFailed:
            return "A processed image could not be encoded as JPEG"
        case .imageMetadataVerificationFailed:
            return "A processed JPEG failed metadata or image verification"
        case .outputWriteFailed:
            return "A processed image could not be written"
        case .manifestWriteFailed:
            return "The photo manifest could not be written"
        }
    }
}

func parseOptions(_ arguments: [String]) throws -> Options {
    var values: [String: String] = [:]
    var index = 0
    while index < arguments.count {
        let name = arguments[index]
        guard ["--input", "--output", "--max-dimension", "--quality"].contains(name),
              index + 1 < arguments.count else {
            throw ProcessingError.usage
        }
        values[name] = arguments[index + 1]
        index += 2
    }

    guard let inputPath = values["--input"], let outputPath = values["--output"] else {
        throw ProcessingError.usage
    }
    let maxDimension = Int(values["--max-dimension"] ?? "2048") ?? 0
    let quality = Double(values["--quality"] ?? "0.88") ?? -1
    guard maxDimension > 0, maxDimension <= 16_384, quality > 0, quality <= 1 else {
        throw ProcessingError.invalidOptions
    }

    return Options(
        inputDirectory: URL(fileURLWithPath: inputPath, isDirectory: true).standardizedFileURL,
        outputDirectory: URL(fileURLWithPath: outputPath, isDirectory: true).standardizedFileURL,
        maxDimension: maxDimension,
        quality: quality
    )
}

func imageFiles(in directory: URL) throws -> [URL] {
    let supportedExtensions: Set<String> = [
        "jpg", "jpeg", "heic", "heif", "png", "tif", "tiff", "bmp", "gif", "webp", "avif",
        "dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf", "pef", "srw"
    ]
    let enumerator = FileManager.default.enumerator(
        at: directory,
        includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey],
        options: [.skipsHiddenFiles, .skipsPackageDescendants]
    )
    guard let enumerator else { throw ProcessingError.invalidInputDirectory }

    var files: [URL] = []
    for case let url as URL in enumerator {
        let properties = try? url.resourceValues(forKeys: [.isRegularFileKey])
        guard properties?.isRegularFile == true,
              supportedExtensions.contains(url.pathExtension.lowercased()) else {
            continue
        }
        files.append(url)
    }
    return files.sorted { $0.path.localizedStandardCompare($1.path) == .orderedAscending }
}

func resizedSRGBImage(from sourceURL: URL, maxDimension: Int) throws -> CGImage {
    guard let source = CGImageSourceCreateWithURL(sourceURL as CFURL, nil),
          CGImageSourceGetCount(source) > 0 else {
        throw ProcessingError.imageDecodeFailed
    }
    let thumbnailOptions: CFDictionary = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: maxDimension,
        kCGImageSourceShouldCacheImmediately: true
    ] as CFDictionary
    guard let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, thumbnailOptions),
          let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else {
        throw ProcessingError.imageDecodeFailed
    }

    let rowBytes = thumbnail.width * 4
    let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
    guard let context = CGContext(
        data: nil,
        width: thumbnail.width,
        height: thumbnail.height,
        bitsPerComponent: 8,
        bytesPerRow: rowBytes,
        space: colorSpace,
        bitmapInfo: bitmapInfo.rawValue
    ) else {
        throw ProcessingError.imageDecodeFailed
    }
    context.interpolationQuality = .high
    context.draw(
        thumbnail,
        in: CGRect(x: 0, y: 0, width: thumbnail.width, height: thumbnail.height)
    )
    guard let normalized = context.makeImage() else { throw ProcessingError.imageDecodeFailed }
    return normalized
}

func markerIsMetadata(_ marker: UInt8) -> Bool {
    (0xE0...0xEF).contains(marker) || marker == 0xFE
}

func jpegWithoutMetadata(_ input: Data) throws -> Data {
    let bytes = [UInt8](input)
    guard bytes.count >= 4, bytes[0] == 0xFF, bytes[1] == 0xD8 else {
        throw ProcessingError.imageEncodeFailed
    }

    var output = Data([0xFF, 0xD8])
    var cursor = 2
    var scanningEntropy = false
    var entropyStart = 0
    var sawEndOfImage = false

    while cursor < bytes.count {
        if scanningEntropy {
            var markerStart = cursor
            while markerStart + 1 < bytes.count {
                if bytes[markerStart] == 0xFF {
                    let following = bytes[markerStart + 1]
                    if following == 0x00 || (0xD0...0xD7).contains(following) {
                        markerStart += 2
                        continue
                    }
                    break
                }
                markerStart += 1
            }
            guard markerStart + 1 < bytes.count else { throw ProcessingError.imageEncodeFailed }
            output.append(contentsOf: bytes[entropyStart..<markerStart])
            cursor = markerStart
            scanningEntropy = false
        }

        let markerStart = cursor
        guard bytes[cursor] == 0xFF else { throw ProcessingError.imageEncodeFailed }
        while cursor < bytes.count && bytes[cursor] == 0xFF { cursor += 1 }
        guard cursor < bytes.count else { throw ProcessingError.imageEncodeFailed }
        let marker = bytes[cursor]
        cursor += 1

        if marker == 0xD9 {
            output.append(contentsOf: bytes[markerStart..<cursor])
            sawEndOfImage = true
            break
        }
        if marker == 0xD8 || marker == 0x01 || (0xD0...0xD7).contains(marker) {
            output.append(contentsOf: bytes[markerStart..<cursor])
            continue
        }
        guard cursor + 1 < bytes.count else { throw ProcessingError.imageEncodeFailed }
        let segmentLength = Int(bytes[cursor]) << 8 | Int(bytes[cursor + 1])
        guard segmentLength >= 2 else { throw ProcessingError.imageEncodeFailed }
        let segmentEnd = cursor + segmentLength
        guard segmentEnd <= bytes.count else { throw ProcessingError.imageEncodeFailed }

        if marker != 0xDA && !markerIsMetadata(marker) {
            output.append(contentsOf: bytes[markerStart..<segmentEnd])
        } else if marker == 0xDA {
            output.append(contentsOf: bytes[markerStart..<segmentEnd])
        }

        cursor = segmentEnd
        if marker == 0xDA {
            scanningEntropy = true
            entropyStart = cursor
        }
    }

    guard sawEndOfImage,
          let source = CGImageSourceCreateWithData(output as CFData, nil),
          CGImageSourceGetCount(source) == 1,
          let verifiedImage = CGImageSourceCreateImageAtIndex(source, 0, nil),
          verifiedImage.width > 0,
          verifiedImage.height > 0 else {
        throw ProcessingError.imageMetadataVerificationFailed
    }
    return output
}

func encodeMetadataFreeJPEG(image: CGImage, quality: Double) throws -> Data {
    let buffer = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(
        buffer as CFMutableData,
        "public.jpeg" as CFString,
        1,
        nil
    ) else {
        throw ProcessingError.imageEncodeFailed
    }
    CGImageDestinationAddImage(
        destination,
        image,
        [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary
    )
    guard CGImageDestinationFinalize(destination) else { throw ProcessingError.imageEncodeFailed }
    return try jpegWithoutMetadata(buffer as Data)
}

func prepareOutputDirectory(_ outputURL: URL, inputURL: URL) throws {
    let input = inputURL.resolvingSymlinksInPath().standardizedFileURL.path
    let output = outputURL.resolvingSymlinksInPath().standardizedFileURL.path
    if output == input || output.hasPrefix(input + "/") {
        throw ProcessingError.unsafeOutputDirectory
    }

    var isDirectory: ObjCBool = false
    if FileManager.default.fileExists(atPath: outputURL.path, isDirectory: &isDirectory) {
        guard isDirectory.boolValue else { throw ProcessingError.outputDirectoryNotEmpty }
        let contents = try FileManager.default.contentsOfDirectory(atPath: outputURL.path)
        guard contents.isEmpty else { throw ProcessingError.outputDirectoryNotEmpty }
    } else {
        try FileManager.default.createDirectory(
            at: outputURL,
            withIntermediateDirectories: true,
            attributes: nil
        )
    }
}

func process(options: Options) throws -> Int {
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(atPath: options.inputDirectory.path, isDirectory: &isDirectory),
          isDirectory.boolValue else {
        throw ProcessingError.invalidInputDirectory
    }
    try prepareOutputDirectory(options.outputDirectory, inputURL: options.inputDirectory)

    let sources = try imageFiles(in: options.inputDirectory)
    guard !sources.isEmpty else { throw ProcessingError.noImagesFound }

    var records: [PhotoRecord] = []
    var seenIDs = Set<String>()
    for sourceURL in sources {
        let image = try resizedSRGBImage(from: sourceURL, maxDimension: options.maxDimension)
        let jpeg = try encodeMetadataFreeJPEG(image: image, quality: options.quality)
        // Hash the exact sanitized bytes that the app will serve; filenames and EXIF never enter the ID.
        let id = SHA256.hash(data: jpeg).map { String(format: "%02x", $0) }.joined()
        guard seenIDs.insert(id).inserted else { continue }

        let outputURL = options.outputDirectory.appendingPathComponent("\(id).jpg")
        do {
            try jpeg.write(to: outputURL, options: .atomic)
        } catch {
            throw ProcessingError.outputWriteFailed
        }
        records.append(PhotoRecord(id: id, object_key: "photos/\(id).jpg"))
    }

    records.sort { $0.id < $1.id }
    let manifestData: Data
    do {
        manifestData = try JSONEncoder.sortedPretty.encode(PhotoManifest(photos: records))
        try manifestData.write(
            to: options.outputDirectory.appendingPathComponent("manifest.json"),
            options: .atomic
        )
    } catch {
        throw ProcessingError.manifestWriteFailed
    }
    return records.count
}

extension JSONEncoder {
    static var sortedPretty: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }
}

do {
    let options = try parseOptions(Array(CommandLine.arguments.dropFirst()))
    let count = try process(options: options)
    print("Processed \(count) image(s) into resized metadata-free JPEGs.")
} catch let error as ProcessingError {
    fputs("process_photos: \(error.description)\n", stderr)
    exit(1)
} catch {
    fputs("process_photos: Image processing failed.\n", stderr)
    exit(1)
}
