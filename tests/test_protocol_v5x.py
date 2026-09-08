from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PaperMode, PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families.v5x import V5X_FINALIZE_PACKET, build_sign_response, split_print_stream
from timiniprint.protocol.packet import make_packet
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class V5XProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = PrinterProtocol(PrinterCatalog.load().detect_device("MXW01"))

    def test_start_uses_selected_media_not_label_capability(self) -> None:
        raster = RasterSet.from_single(RasterBuffer(
            pixels=[0] * (384 * 257), width=384, pixel_format=PixelFormat.BW1,
        ))
        self.assertTrue(self.protocol.device.profile.can_print_label)
        self.assertEqual(self.protocol.supported_paper_modes(), (PaperMode.PLAIN, PaperMode.TAG))
        for paper_mode, mode_byte in ((None, 0), (PaperMode.PLAIN, 0), (PaperMode.TAG, 1)):
            with self.subTest(paper_mode=paper_mode):
                job = self.protocol.build_job(raster, is_text=False, paper_mode=paper_mode)
                split = split_print_stream(job.payload)
                self.assertEqual([packet[2] for packet in split.commands], [0xA2, 0xA9])
                self.assertEqual(split.commands[-1], bytes.fromhex("2221a9000400010130") + bytes([mode_byte, 0, 0]))
                self.assertEqual(len(split.bulk_payload), 48 * 257)

    def test_gray_start_declares_mode_for_both_pixel_formats(self) -> None:
        for fmt, pixels, expected in (
            (PixelFormat.GRAY4, [15, 0] * 192, b"\xf0" * 192),
            (PixelFormat.GRAY8, [0, 255] * 192, b"\x00\xff" * 192),
        ):
            with self.subTest(fmt=fmt):
                raster = RasterSet.from_single(RasterBuffer(pixels=pixels, width=384, pixel_format=fmt))
                job = self.protocol.build_job(
                    raster, is_text=False, image_encoding_override=ImageEncoding.V5X_GRAY,
                    pixel_format_override=fmt,
                )
                split = split_print_stream(job.payload)
                self.assertEqual(split.commands[-1], bytes.fromhex("2221a9000400010030020000"))
                self.assertEqual(split.bulk_payload, expected)

    def test_raster_prefix_and_suffix_are_never_parsed_as_commands(self) -> None:
        for raw in (
            make_packet(0xA4, b"\x00\x00", ProtocolFamily.V5X) + bytes(38),
            V5X_FINALIZE_PACKET + bytes(30) + V5X_FINALIZE_PACKET,
        ):
            with self.subTest(raw=raw):
                pixels = [(value >> bit) & 1 for value in raw for bit in range(8)]
                raster = RasterSet.from_single(RasterBuffer(pixels=pixels, width=384, pixel_format=PixelFormat.BW1))
                job = self.protocol.build_job(raster, is_text=False)
                split = split_print_stream(job.payload)
                self.assertEqual([packet[2] for packet in split.commands], [0xA2, 0xA9])
                self.assertEqual(split.bulk_payload, raw)
                self.assertEqual(split.trailing_commands, (V5X_FINALIZE_PACKET,))

    def test_control_only_stream_and_malformed_jobs(self) -> None:
        control = make_packet(0xA3, b"\x05\x00", ProtocolFamily.V5X)
        self.assertEqual(split_print_stream(control).commands, (control,))
        self.assertEqual(split_print_stream(control).bulk_payload, b"")
        for data in (control[:-1], bytes.fromhex("2221a9000400010030000000") + bytes(48)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                split_print_stream(data)

    def test_local_sign_response_matches_fixed_vectors(self) -> None:
        vectors = (
            ("00010203040506070809", 1788900001234, "2221B30022003B22C352E178212E225C191E5C441639603246B9E4377164905DEF6B707D0CFE50F400FF"),
            ("ffffffffffffffffffFF", 1788900009999, "2221B3002200E263E07B9B4E157BFF9942971CAC51756C8DAEAB6A447F09368DA434E17763CA557900FF"),
            ("00112233445566778899", 1788900000001, "2221B3002200550149BF2A721C04E648DE9461373076EA30C1B23621F1454941948D3E4D00BCA89D00FF"),
        )
        for challenge, timestamp_ms, expected in vectors:
            with self.subTest(timestamp_ms=timestamp_ms):
                self.assertEqual(build_sign_response(bytes.fromhex(challenge), timestamp_ms=timestamp_ms), bytes.fromhex(expected))
        for challenge in (b"", bytes(9), bytes(11)):
            with self.subTest(length=len(challenge)), self.assertRaises(ValueError):
                build_sign_response(challenge, timestamp_ms=1234)
