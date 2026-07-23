from __future__ import annotations

import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PrinterProtocol, ProtocolStepOperation
from timiniprint.protocol.families.niimbot.core import (
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

        page_size = next(step for step in job.steps if step.label == "set page size")
        self.assertEqual(parse_packets(page_size.data)[0].data, b"\x00\x02\x00\x60")

        ack = frame(NiimbotResponse.SET_DENSITY, b"\x01")
        self.assertTrue(job.steps[0].reply_matcher.matches(ack))

    def test_d110_task_uses_raster_width_for_row_geometry(self) -> None:
        device = PrinterCatalog.load().device_from_profile("niimbot_d110")
        raster = RasterBuffer(pixels=[1] * 384, width=384, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            page_count=1,
        )

        page_size = next(step for step in job.steps if step.label == "set page size")
        self.assertEqual(parse_packets(page_size.data)[0].data, b"\x00\x01\x01\x80")

        image_row = next(step for step in job.steps if step.label == "image row 0")
        row_packet = parse_packets(image_row.data)[0]
        self.assertEqual(row_packet.command, int(NiimbotRequest.PRINT_BITMAP_ROW))
        self.assertEqual(row_packet.data[:6], b"\x00\x00\x80\x80\x80\x01")
        self.assertEqual(row_packet.data[6:], b"\xff" * 48)

    def test_row_encoder_can_use_total_counts_and_check_lines(self) -> None:
        from timiniprint.protocol.families.niimbot.core import _NiimbotRowEncoder

        raster = RasterBuffer(
            pixels=[1] * (384 * 201),
            width=384,
            pixel_format=PixelFormat.BW1,
        )

        steps = _NiimbotRowEncoder(
            counts_mode="total",
            check_line_interval=200,
        ).build_steps(raster)

        self.assertEqual(
            [step.label for step in steps],
            ["image row 0", "check line 199", "image row 200"],
        )
        first_row = parse_packets(steps[0].data)[0]
        self.assertEqual(first_row.data[:6], b"\x00\x00\x00\x80\x01\xc8")
        check_line = parse_packets(steps[1].data)[0]
        self.assertEqual(check_line.command, int(NiimbotRequest.PRINTER_CHECK_LINE))
        self.assertEqual(check_line.data, b"\x00\xc7\x01")
        self.assertEqual(steps[1].operation, ProtocolStepOperation.QUERY)
        self.assertFalse(
            steps[1].reply_matcher.matches(
                frame(NiimbotResponse.PRINTER_CHECK_LINE, b"\x00\xc7\x00")
            )
        )
        self.assertTrue(
            steps[1].reply_matcher.matches(
                frame(NiimbotResponse.PRINTER_CHECK_LINE, b"\x00\xc7\x01")
            )
        )

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

    def test_d11_profile_uses_old_d11_page_size_and_page_index_wait(self) -> None:
        device = PrinterCatalog.load().device_from_profile("niimbot_d11")
        raster = RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            page_count=1,
        )

        labels = [step.label for step in job.steps]
        self.assertIn("set density", labels)
        self.assertIn("page index", labels)
        self.assertNotIn("print status", labels)

        page_size = next(step for step in job.steps if step.label == "set page size")
        self.assertEqual(page_size.data[2], int(NiimbotRequest.SET_PAGE_SIZE))
        self.assertEqual(page_size.data[3], 2)
        self.assertEqual(parse_packets(page_size.data)[0].data, b"\x00\x01")

        page_index = next(step for step in job.steps if step.label == "page index")
        self.assertEqual(page_index.operation, ProtocolStepOperation.WAIT)
        self.assertFalse(page_index.include_in_payload)
        self.assertTrue(
            page_index.reply_matcher.matches(
                frame(NiimbotResponse.PRINTER_PAGE_INDEX, b"\x00\x01")
            )
        )
        self.assertEqual(job.steps[-1].data[2], int(NiimbotRequest.PRINT_END))

    def test_d11_multipage_job_waits_for_the_final_page_index_only(self) -> None:
        device = PrinterCatalog.load().device_from_profile("niimbot_d11")
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
        self.assertNotIn("page index", first_labels)
        self.assertNotIn("print end", first_labels)
        self.assertIn("page index", second_labels)
        self.assertIn("print end", second_labels)

        page_index = next(step for step in second.steps if step.label == "page index")
        self.assertFalse(
            page_index.reply_matcher.matches(
                frame(NiimbotResponse.PRINTER_PAGE_INDEX, b"\x00\x01")
            )
        )
        self.assertTrue(
            page_index.reply_matcher.matches(
                frame(NiimbotResponse.PRINTER_PAGE_INDEX, b"\x00\x02")
            )
        )

    def test_d11_and_d110_keep_the_same_row_encoding(self) -> None:
        pixels = [0] * 96 + [1] + [0] * 95
        raster = RasterBuffer(pixels=pixels, width=96, pixel_format=PixelFormat.BW1)

        encoded_rows = []
        for profile_key in ("niimbot_d11", "niimbot_d110"):
            device = PrinterCatalog.load().device_from_profile(profile_key)
            job = PrinterProtocol(device).build_job(
                RasterSet.from_single(raster),
                is_text=False,
                page_count=1,
            )
            encoded_rows.append(
                [step.data for step in job.steps if step.label.startswith("image row ")]
            )

        self.assertEqual(encoded_rows[0], encoded_rows[1])
        self.assertEqual(
            [parse_packets(packet)[0].command for packet in encoded_rows[0]],
            [
                int(NiimbotRequest.PRINT_EMPTY_ROW),
                int(NiimbotRequest.PRINT_BITMAP_ROW_INDEXED),
            ],
        )

    def test_catalog_detects_d110_as_niimbot(self) -> None:
        detected = PrinterCatalog.load().detect_device("D110-1234")

        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.profile_key, "niimbot_d110")
        self.assertEqual(detected.protocol_family.value, "niimbot")
        self.assertEqual(detected.protocol_variant, "d110")

    def test_catalog_detects_d11_as_niimbot(self) -> None:
        for name in ("D11", "D11-1234", "D11_1234", "D11S", "D11S-1234", "D11S_1234"):
            with self.subTest(name=name):
                detected = PrinterCatalog.load().detect_device(name)

                self.assertIsNotNone(detected)
                assert detected is not None
                self.assertEqual(detected.profile_key, "niimbot_d11")
                self.assertEqual(detected.protocol_family.value, "niimbot")
                self.assertEqual(detected.protocol_variant, "d11_v1")

    def test_catalog_does_not_treat_d110_m_as_d110(self) -> None:
        detected = PrinterCatalog.load().detect_device("D110_M")

        self.assertIsNone(detected)


if __name__ == "__main__":
    unittest.main()
