from __future__ import annotations

import unittest

from PIL import Image

from timiniprint.devices import BluetoothTransportPolicy, PrinterCatalog
from timiniprint.devices.device import BluetoothEndpoint, BluetoothEndpointTransport
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.settings import ImageMode, PrintSettings
from timiniprint.protocol import PaperMode, PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class ElephTsplProtocolTests(unittest.TestCase):
    def test_p1_profile_builds_eleph_label_bitmap_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_tspl_p1")
        pixels = [0] * (384 * 8)
        pixels[0] = 1
        raster = RasterBuffer(pixels=pixels, width=384, pixel_format=PixelFormat.BW1)

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=3,
        )

        expected_order = (
            b"SIZE 48 mm,1 mm\r\n",
            b"GAP 2 mm,0 mm\r\n",
            b"DIRECTION 0\r\n",
            b"CLS\r\n",
            b"BITMAP 0,0,48,8,0,\x7f",
            b"PRINT 1,1\r\n",
        )
        positions = [job.payload.index(marker) for marker in expected_order]
        self.assertEqual(positions, sorted(positions))
        self.assertFalse(job.payload.startswith(b"\x10\xff\x10\x03"))
        self.assertNotIn(b"SET RIBBON", job.payload)
        self.assertNotIn(b"REFERENCE", job.payload)
        self.assertNotIn(b"SPEED ", job.payload)
        self.assertNotIn(b"DENSITY ", job.payload)

    def test_p1_default_direction_does_not_rotate_or_mirror_the_raster(self) -> None:
        coordinates = [(0, 0), (8, 1), (31, 2), (383, 23)]
        image = Image.new("RGB", (384, 24), "white")
        for point in coordinates:
            image.putpixel(point, (0, 0, 0))
        renderer = DocumentRenderer(image_loader=lambda _path: image.copy())
        settings = PrintSettings(
            image_mode=ImageMode.THRESHOLD,
            trim_side_margins=False,
            trim_top_bottom_margins=False,
        )
        for name in ("P1_A57F", "P1_F30E"):
            with self.subTest(name=name):
                device = PrinterCatalog.load().detect_device(name)
                plan = renderer.plan_document(RenderDocument("orientation.png"), device, settings)
                rendered = renderer.print_page(plan, plan.pages[0], device, settings)
                job = PrinterProtocol(device).build_job(rendered.raster_set, is_text=False)

                self.assertEqual(job.payload.count(b"DIRECTION 0\r\n"), 1)
                self.assertNotIn(b"DIRECTION 1\r\n", job.payload)
                marker = b"BITMAP 0,0,48,24,0,"
                offset = job.payload.index(marker) + len(marker)
                bitmap = job.payload[offset : offset + 48 * 24]
                actual = [
                    (x, y) for y in range(24) for x in range(384)
                    if not (bitmap[y * 48 + x // 8] >> (7 - x % 8)) & 1
                ]
                self.assertEqual(actual, coordinates)

    def test_eleph_profile_prefers_spp_without_ble_pacing_or_toprint_media_modes(self) -> None:
        device = PrinterCatalog.load().device_from_profile("eleph_tspl_p1")

        self.assertEqual(device.protocol_family, ProtocolFamily.ELEPH_TSPL)
        self.assertEqual(device.protocol_variant, "p1")
        self.assertEqual(device.image_pipeline.encoding, ImageEncoding.ELEPH_TSPL_BITMAP)
        self.assertTrue(device.profile.use_spp)
        self.assertEqual(PrinterProtocol(device).supported_paper_modes(), (PaperMode.TAG,))
        self.assertEqual(device.profile.stream.chunk_size, 1024)
        self.assertEqual(device.profile.stream.delay_ms, 0)
        self.assertEqual(device.ble_transport_profile.standard_chunk_cap, 20)
        self.assertEqual(device.ble_transport_profile.standard_write_delay_ms, 30)

    def test_eleph_p1_prefers_classic_and_keeps_ble_fallback(self) -> None:
        policy = BluetoothTransportPolicy(PrinterCatalog.load())
        for name in ("P1_A57F", "P1_F30E"):
            with self.subTest(name=name):
                endpoints = [
                    BluetoothEndpoint(
                        name=name,
                        address="AA:BB:CC:DD:EE:01",
                        transport=transport,
                    )
                    for transport in (
                        BluetoothEndpointTransport.BLE,
                        BluetoothEndpointTransport.CLASSIC,
                    )
                ]
                devices = policy.devices_from_endpoints(endpoints)
                self.assertEqual(len(devices), 1)
                device = devices[0]
                self.assertEqual(device.profile_key, "eleph_tspl_p1")
                self.assertEqual(device.protocol_variant, "p1")
                attempts = policy.connection_plan(device).attempts
                self.assertEqual(
                    [attempt.endpoint.transport for attempt in attempts],
                    [BluetoothEndpointTransport.CLASSIC, BluetoothEndpointTransport.BLE],
                )
                self.assertTrue(attempts[1].is_fallback)

                ble_only = policy.devices_from_endpoints(endpoints[:1])[0]
                self.assertEqual(ble_only.profile_key, "eleph_tspl_p1")
                self.assertEqual(policy.connection_plan(ble_only).endpoints, (endpoints[0],))

    def test_catalog_detects_eleph_p1_without_stealing_other_p1_profiles(self) -> None:
        catalog = PrinterCatalog.load()

        eleph = catalog.detect_device("P1_F30E")
        self.assertIsNotNone(eleph)
        assert eleph is not None
        self.assertEqual(eleph.profile_key, "eleph_tspl_p1")
        self.assertEqual(eleph.protocol_family, ProtocolFamily.ELEPH_TSPL)

        for name in ("P1", "P1-1234"):
            matches = catalog.detect_model(name)
            self.assertEqual(
                {candidate.model.model_key for candidate in matches},
                {"pocket_printer", "toprint_tspl_p1"},
            )
            self.assertIsNone(catalog.detect_device(name))


if __name__ == "__main__":
    unittest.main()
