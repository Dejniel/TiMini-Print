from __future__ import annotations

import unittest

from tests.helpers import build_capture_reporter
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.debug_dump import (
    build_protocol_packet_entries,
    build_protocol_packet_summary,
)
from timiniprint.printing.diagnostics import report_protocol_job_build
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import ImageEncoding, PrinterProtocol, ProtocolJob
from timiniprint.protocol.families.v5x import V5X_FINALIZE_PACKET, V5X_GET_SERIAL_PACKET
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.packet import make_packet
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class ProtocolPacketDiagnosticsTests(unittest.TestCase):
    def test_v5x_dot_and_gray_jobs_report_raw_raster_without_parse_errors(self) -> None:
        device = PrinterCatalog.load().device_from_profile("v5x")
        protocol = PrinterProtocol(device)
        for pixel_format, encoding, raster_bytes in (
            (PixelFormat.BW1, ImageEncoding.V5X_DOT, 96),
            (PixelFormat.GRAY4, ImageEncoding.V5X_GRAY, 384),
            (PixelFormat.GRAY8, ImageEncoding.V5X_GRAY, 768),
        ):
            with self.subTest(pixel_format=pixel_format):
                pipeline = protocol.resolve_image_pipeline(
                    image_encoding_override=encoding, pixel_format_override=pixel_format
                )
                raster = RasterBuffer(pixels=[1] * 768, width=384, pixel_format=pixel_format)
                job = protocol.build_job(
                    RasterSet.from_single(raster), is_text=False, image_pipeline=pipeline
                )
                entries = build_protocol_packet_entries(device, job.payload)
                self.assertEqual([entry["op"] for entry in entries], ["A2", "A9", None, "AD"])
                self.assertEqual(entries[2]["payload_bytes"], raster_bytes)
                self.assertEqual(sum(entry["bytes"] for entry in entries), len(job.payload))
                offset = 0
                for index, entry in enumerate(entries):
                    self.assertEqual(entry["offset"], offset)
                    self.assertEqual(entry["index"], index)
                    offset += entry["bytes"]
                summary = build_protocol_packet_summary(device, job.payload)
                self.assertEqual(summary["op_counts"], {"A2": 1, "A9": 1, "raw": 1, "AD": 1})
                self.assertEqual(summary["parse_errors"], [])

                self.assertTrue(job.steps)
                for reported_job in (job, ProtocolJob(payload=job.payload)):
                    reporter, sink = build_capture_reporter()
                    report_protocol_job_build(
                        reporter, device=device, settings=PrintSettings(),
                        job=reported_job, pipeline=pipeline, page_count=1,
                    )
                    packet_log = next(message.detail for message in sink.messages if "Protocol packets" in message.detail)
                    self.assertIn("parse_errors=<none>", packet_log)
                    self.assertIn("raw:1", packet_log)

    def test_v5x_raster_is_opaque_even_when_pixels_look_like_commands(self) -> None:
        device = PrinterCatalog.load().device_from_profile("v5x")
        start = make_packet(0xA9, bytes.fromhex("010030000000"), ProtocolFamily.V5X)
        raw = make_packet(0xA4, b"\x00\x00", ProtocolFamily.V5X) + V5X_FINALIZE_PACKET
        for footer in (b"", V5X_FINALIZE_PACKET):
            # Without a footer, use a raster that does not end with the footer marker.
            raster = raw if footer else raw + b"\x55"
            with self.subTest(footer=footer):
                payload = V5X_GET_SERIAL_PACKET + start + raster + footer
                entries = build_protocol_packet_entries(device, payload)
                self.assertEqual(
                    [entry["op"] for entry in entries],
                    ["A7", "A9", None] + (["AD"] if footer else []),
                )
                self.assertEqual(entries[2]["bytes"], len(raster))
                self.assertEqual(entries[2]["packet_head"], raster[:24].hex())
                self.assertEqual(build_protocol_packet_summary(device, payload)["parse_errors"], [])

    def test_v5x_incomplete_commands_still_report_parse_errors(self) -> None:
        device = PrinterCatalog.load().device_from_profile("v5x")
        truncated = make_packet(0xA9, bytes.fromhex("010030000000"), ProtocolFamily.V5X)[:-1]
        for payload in (truncated, V5X_GET_SERIAL_PACKET + truncated, b"unframed data"):
            with self.subTest(payload=payload):
                summary = build_protocol_packet_summary(device, payload)
                self.assertEqual(summary["parse_errors"], ["not a complete prefixed packet"])

    def test_other_prefixed_families_still_report_trailing_garbage(self) -> None:
        device = PrinterCatalog.load().device_from_profile("d1")
        payload = make_packet(0xA9, b"\x00", device.protocol_family) + b"unframed data"

        summary = build_protocol_packet_summary(device, payload)

        self.assertEqual(summary["op_counts"], {"A9": 1, "raw": 1})
        self.assertEqual(summary["parse_errors"], ["not a complete prefixed packet"])

    def test_unprefixed_protocols_still_report_one_raw_payload(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_tspl_p1")
        payload = b"CLS\r\nPRINT 1,1\r\n"

        summary = build_protocol_packet_summary(device, payload)

        self.assertEqual(summary["op_counts"], {"raw": 1})
        self.assertEqual(summary["parse_errors"], [])


if __name__ == "__main__":
    unittest.main()
