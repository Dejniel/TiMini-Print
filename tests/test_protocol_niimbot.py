from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PrinterProtocol
from timiniprint.protocol.families.niimbot_core import (
    NiimbotRequest,
    NiimbotResponse,
    frame,
    parse_packets,
)
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class NiimbotProtocolTests(unittest.TestCase):
    def test_frame_matches_niimbot_xor_packet_shape(self) -> None:
        self.assertEqual(
            frame(NiimbotRequest.CONNECT),
            bytes.fromhex("03 55 55 c1 01 01 c1 aa aa"),
        )
        self.assertEqual(
            frame(NiimbotRequest.SET_DENSITY, b"\x02"),
            bytes.fromhex("55 55 21 01 02 22 aa aa"),
        )

    def test_parse_packets_rejects_bad_checksum(self) -> None:
        with self.assertRaisesRegex(ValueError, "checksum"):
            parse_packets(bytes.fromhex("55 55 21 01 02 00 aa aa"))

    def test_d110_profile_builds_response_matched_row_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("niimbot_d110")
        pixels = [0] * 96 + [1, 0, 0, 0, 0, 0, 0, 0] + [0] * 88
        raster = RasterBuffer(pixels=pixels, width=96, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            page_count=1,
        )

        self.assertGreater(len(job.steps), 0)
        self.assertEqual(job.steps[0].label, "set density")
        self.assertTrue(all(step.reply_matcher is not None for step in job.steps[:7]))
        self.assertTrue(any(step.data[2] == int(NiimbotRequest.PRINT_EMPTY_ROW) for step in job.steps))
        self.assertTrue(any(step.data[2] == int(NiimbotRequest.PRINT_BITMAP_ROW_INDEXED) for step in job.steps))
        self.assertEqual(job.steps[-1].data[2], int(NiimbotRequest.PRINT_END))

        ack = frame(NiimbotResponse.SET_DENSITY, b"\x01")
        self.assertTrue(job.steps[0].reply_matcher.matches(ack))

    def test_d110_multipage_job_keeps_one_print_task_boundary(self) -> None:
        device = PrinterCatalog.load().device_from_profile("niimbot_d110")
        raster = RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1)
        protocol = PrinterProtocol(device)

        first = protocol.build_job(
            RasterSet.from_single(raster),
            is_text=False,
            page_index=1,
            page_count=2,
        )
        second = protocol.build_job(
            RasterSet.from_single(raster),
            is_text=False,
            page_index=2,
            page_count=2,
        )

        first_labels = [step.label for step in first.steps]
        second_labels = [step.label for step in second.steps]
        self.assertIn("print start", first_labels)
        self.assertNotIn("print end", first_labels)
        self.assertNotIn("print start", second_labels)
        self.assertIn("print end", second_labels)

        first_status = next(step for step in first.steps if step.label == "print status")
        second_status = next(step for step in second.steps if step.label == "print status")
        self.assertTrue(
            first_status.reply_matcher.matches(frame(NiimbotResponse.PRINT_STATUS, b"\x00\x01"))
        )
        self.assertFalse(
            second_status.reply_matcher.matches(frame(NiimbotResponse.PRINT_STATUS, b"\x00\x01"))
        )
        self.assertTrue(
            second_status.reply_matcher.matches(frame(NiimbotResponse.PRINT_STATUS, b"\x00\x02"))
        )

    def test_catalog_detects_d110_as_niimbot(self) -> None:
        detected = PrinterCatalog.load().detect_device("D110-1234")

        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.profile_key, "niimbot_d110")
        self.assertEqual(detected.protocol_family.value, "niimbot")
        self.assertEqual(detected.protocol_variant, "d110")

    def test_catalog_does_not_treat_d110_m_as_d110(self) -> None:
        detected = PrinterCatalog.load().detect_device("D110_M")

        self.assertIsNone(detected)


if __name__ == "__main__":
    unittest.main()
