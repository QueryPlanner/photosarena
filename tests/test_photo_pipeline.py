from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSOR = PROJECT_ROOT / "tools" / "process_photos.swift"


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def synthetic_png(width: int, height: int) -> bytes:
    row = b"\x00" + bytes((231, 77, 35)) * width
    raw_pixels = row * height
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(raw_pixels))
        + png_chunk(b"IEND", b"")
    )


def exif_with_orientation_and_gps() -> bytes:
    # IFD0 stores orientation 6 and points to a GPS IFD containing a latitude.
    ifd0_offset = 8
    gps_ifd_offset = 38
    gps_data_offset = 68
    tiff = bytearray(b"II" + struct.pack("<HI", 42, ifd0_offset))
    tiff += struct.pack("<H", 2)
    tiff += struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<H", 6) + b"\x00\x00"
    tiff += struct.pack("<HHII", 0x8825, 4, 1, gps_ifd_offset)
    tiff += struct.pack("<I", 0)
    assert len(tiff) == gps_ifd_offset
    tiff += struct.pack("<H", 2)
    tiff += struct.pack("<HHI", 0x0001, 2, 2) + b"N\x00\x00\x00"
    tiff += struct.pack("<HHII", 0x0002, 5, 3, gps_data_offset)
    tiff += struct.pack("<I", 0)
    assert len(tiff) == gps_data_offset
    tiff += struct.pack("<IIIIII", 37, 1, 46, 1, 1234, 100)
    return b"Exif\x00\x00" + bytes(tiff)


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg_segments(jpeg: bytes) -> list[tuple[int, bytes]]:
    if not jpeg.startswith(b"\xff\xd8"):
        raise AssertionError("Expected JPEG SOI marker")
    result: list[tuple[int, bytes]] = []
    cursor = 2
    while cursor < len(jpeg):
        if jpeg[cursor] != 0xFF:
            raise AssertionError("Malformed JPEG marker sequence")
        while cursor < len(jpeg) and jpeg[cursor] == 0xFF:
            cursor += 1
        marker = jpeg[cursor]
        cursor += 1
        if marker == 0xDA:
            break
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            result.append((marker, b""))
            if marker == 0xD9:
                break
            continue
        if cursor + 2 > len(jpeg):
            raise AssertionError("Truncated JPEG segment length")
        segment_length = struct.unpack_from(">H", jpeg, cursor)[0]
        if segment_length < 2 or cursor + segment_length > len(jpeg):
            raise AssertionError("Invalid JPEG segment length")
        payload = jpeg[cursor + 2 : cursor + segment_length]
        result.append((marker, payload))
        cursor += segment_length
    return result


def jpeg_marker_codes(jpeg: bytes) -> list[int]:
    if not jpeg.startswith(b"\xff\xd8"):
        raise AssertionError("Expected JPEG SOI marker")
    markers = [0xD8]
    cursor = 2
    in_entropy = False
    while cursor < len(jpeg):
        if in_entropy:
            marker_start = cursor
            while marker_start + 1 < len(jpeg):
                if jpeg[marker_start] == 0xFF:
                    following = jpeg[marker_start + 1]
                    if following == 0x00 or 0xD0 <= following <= 0xD7:
                        marker_start += 2
                        continue
                    break
                marker_start += 1
            if marker_start + 1 >= len(jpeg):
                raise AssertionError("JPEG scan has no terminating marker")
            cursor = marker_start
            in_entropy = False

        if jpeg[cursor] != 0xFF:
            raise AssertionError("Malformed JPEG marker sequence")
        while cursor < len(jpeg) and jpeg[cursor] == 0xFF:
            cursor += 1
        marker = jpeg[cursor]
        cursor += 1
        markers.append(marker)
        if marker == 0xD9:
            break
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if cursor + 2 > len(jpeg):
            raise AssertionError("Truncated JPEG segment length")
        segment_length = struct.unpack_from(">H", jpeg, cursor)[0]
        if segment_length < 2 or cursor + segment_length > len(jpeg):
            raise AssertionError("Invalid JPEG segment length")
        cursor += segment_length
        if marker == 0xDA:
            in_entropy = True
    if not markers or markers[-1] != 0xD9:
        raise AssertionError("JPEG has no end marker")
    return markers


def jpeg_dimensions(jpeg: bytes) -> tuple[int, int]:
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    for marker, payload in jpeg_segments(jpeg):
        if marker in start_of_frame:
            if len(payload) < 5:
                raise AssertionError("Truncated JPEG frame header")
            height, width = struct.unpack_from(">HH", payload, 1)
            return width, height
    raise AssertionError("JPEG has no frame header")


def add_synthetic_metadata(jpeg: bytes) -> bytes:
    exif = jpeg_segment(0xE1, exif_with_orientation_and_gps())
    xmp = jpeg_segment(
        0xE1,
        b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>synthetic fixture</x:xmpmeta>",
    )
    comment = jpeg_segment(0xFE, b"synthetic-private-comment")
    return jpeg[:2] + exif + xmp + comment + jpeg[2:]


@unittest.skipUnless(
    sys.platform == "darwin" and shutil.which("swift") and shutil.which("sips"),
    "The image processor uses macOS ImageIO and is tested on macOS",
)
class PhotoPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.input_dir = self.root / "input"
        self.output_dir = self.root / "processed"
        self.input_dir.mkdir()

        png_path = self.input_dir / "synthetic-source-with-private-name.png"
        png_path.write_bytes(synthetic_png(32, 20))
        raw_jpeg_path = self.root / "raw-fixture.jpg"
        subprocess.run(
            ["sips", "-s", "format", "jpeg", str(png_path), "--out", str(raw_jpeg_path)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        fixture_jpeg = add_synthetic_metadata(raw_jpeg_path.read_bytes())
        source_path = self.input_dir / "synthetic-source-with-private-name.jpg"
        source_path.write_bytes(fixture_jpeg)
        png_path.unlink()
        source_markers = jpeg_marker_codes(fixture_jpeg)
        self.assertIn(0xE1, source_markers)
        self.assertIn(0xFE, source_markers)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_processor(self, output_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
        cache_dir = self.root / "swift-module-cache"
        cache_dir.mkdir(exist_ok=True)
        env = os.environ.copy()
        env["CLANG_MODULE_CACHE_PATH"] = str(cache_dir)
        env["SWIFT_MODULE_CACHE_PATH"] = str(cache_dir)
        return subprocess.run(
            [
                "swift",
                str(PROCESSOR),
                "--input",
                str(self.input_dir),
                "--output",
                str(output_dir or self.output_dir),
                "--max-dimension",
                "16",
                "--quality",
                "0.82",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

    def test_resizes_oriented_images_and_strips_all_exif_gps_xmp_and_comments(self) -> None:
        result = self.run_processor()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Processed 1 image(s)", result.stdout)
        manifest_path = self.output_dir / "manifest.json"
        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        self.assertEqual(set(manifest), {"photos"})
        self.assertEqual(len(manifest["photos"]), 1)
        record = manifest["photos"][0]
        self.assertEqual(set(record), {"id", "object_key"})
        photo_id = record["id"]
        self.assertRegex(photo_id, r"^[0-9a-f]{64}$")
        self.assertEqual(record["object_key"], f"photos/{photo_id}.jpg")
        self.assertNotIn("synthetic-source-with-private-name", manifest_text)

        output_path = self.output_dir / f"{photo_id}.jpg"
        output_bytes = output_path.read_bytes()
        self.assertEqual(hashlib.sha256(output_bytes).hexdigest(), photo_id)

        # These checks parse the JPEG independently of the Swift/ImageIO processor.
        width, height = jpeg_dimensions(output_bytes)
        self.assertEqual((width, height), (10, 16))
        metadata_markers = [
            marker
            for marker in jpeg_marker_codes(output_bytes)
            if 0xE0 <= marker <= 0xEF or marker == 0xFE
        ]
        self.assertEqual(metadata_markers, [])

    def test_refuses_to_overwrite_existing_output(self) -> None:
        self.output_dir.mkdir()
        sentinel = self.output_dir / "keep.txt"
        sentinel.write_text("preserve", encoding="utf-8")

        result = self.run_processor()

        self.assertEqual(result.returncode, 1)
        self.assertIn("must be empty", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertEqual(sorted(path.name for path in self.output_dir.iterdir()), ["keep.txt"])


if __name__ == "__main__":
    unittest.main()
