from __future__ import annotations

import unittest
from dataclasses import replace

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.funny_lx import FunnyLxRuntimeController
from timiniprint.protocol import PrinterProtocol, ProtocolStepOperation
from timiniprint.protocol.families.funny_lx.core import challenge_crc
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class _FunnyLxSession:
    def __init__(self, replies: list[bytes]) -> None:
        self.replies = list(replies)
        self.packets: list[bytes] = []
        self.debugs: list[str] = []
        self.notify_started = True

    def can_send_control_packet_wait_notification(self) -> bool:
        return True

    async def send_control_packet_wait_notification(
        self,
        packet: bytes,
        *,
        label: str,
        match,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        _ = label, timeout, required
        self.packets.append(bytes(packet))
        reply = self.replies.pop(0)
        if not match(reply):
            raise AssertionError(f"reply did not match: {reply.hex()}")
        return reply

    def report_debug(self, message: str) -> None:
        self.debugs.append(message)


class FunnyLxProtocolTests(unittest.TestCase):
    def test_crc16_xmodem_matches_observed_lx_d02_handshake(self) -> None:
        mac = bytes.fromhex("c00000000460")

        first = challenge_crc(bytes.fromhex("f7"), mac)
        second = challenge_crc(bytes.fromhex("cf"), mac)

        self.assertEqual(first.low, bytes.fromhex("8e"))
        self.assertEqual(first.high, bytes.fromhex("28"))
        self.assertEqual(second.low, bytes.fromhex("ae"))
        self.assertEqual(second.high, bytes.fromhex("e2"))

    def test_catalog_detects_funny_lx_exact_names(self) -> None:
        catalog = PrinterCatalog.load()
        for name in ("LX-D01", "LX-D02", "LX-D2"):
            with self.subTest(name=name):
                detected = catalog.detect_device(name)

                self.assertIsNotNone(detected)
                assert detected is not None
                self.assertEqual(detected.model_key, "funny_lx_d")
                self.assertEqual(detected.profile_key, "funny_lx_384")
                self.assertEqual(detected.protocol_family, ProtocolFamily.FUNNY_LX)
                self.assertEqual(detected.protocol_variant, "lx_d_direct")
                self.assertEqual(detected.image_pipeline.encoding, ImageEncoding.FUNNY_LX_RASTER)

    def test_catalog_does_not_treat_display_suffix_as_raw_name(self) -> None:
        self.assertIsNone(PrinterCatalog.load().detect_device("LX-D02-60"))

    def test_builds_direct_lx_image_job_by_default(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        first_row = [1, 0, 0, 0, 0, 0, 0, 0] + [0] * 376
        second_row = [0, 1, 0, 0, 0, 0, 0, 0] + [0] * 376
        raster = RasterBuffer(
            pixels=first_row + second_row,
            width=384,
            pixel_format=PixelFormat.BW1,
        )

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=4,
        )

        self.assertEqual([step.label for step in job.steps], [
            "darkness",
            "print header",
            "image packet 1",
            "image accepted",
            "print footer",
        ])
        self.assertEqual(job.steps[0].data, bytes.fromhex("5a 0c 03"))
        self.assertEqual(job.steps[1].data, bytes.fromhex("5a 04 00 01 00 00"))
        self.assertEqual(job.steps[2].data[:3], bytes.fromhex("55 00 00"))
        self.assertEqual(job.steps[2].data[3:51], bytes([0x80]) + bytes(47))
        self.assertEqual(job.steps[2].data[51:99], bytes([0x40]) + bytes(47))
        self.assertEqual(job.steps[2].data[-1], 0)
        self.assertEqual(job.steps[3].operation, ProtocolStepOperation.WAIT)
        self.assertTrue(job.steps[3].reply_matcher.matches(bytes.fromhex("5a 06 00")))
        self.assertEqual(job.steps[4].data, bytes.fromhex("5a 04 00 01 01"))
        self.assertTrue(job.steps[4].reply_matcher.matches(bytes.fromhex("5a 04 00 01 01")))

    def test_builds_reversed_lx_image_job_for_legacy_variant(self) -> None:
        device = replace(PrinterCatalog.load().device_from_model("funny_lx_d"), protocol_variant="lx_d_reversed")
        first_row = [1, 0, 0, 0, 0, 0, 0, 0] + [0] * 376
        second_row = [0, 1, 0, 0, 0, 0, 0, 0] + [0] * 376
        raster = RasterBuffer(
            pixels=first_row + second_row,
            width=384,
            pixel_format=PixelFormat.BW1,
        )

        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=4,
        )

        self.assertEqual(job.steps[2].data[:3], bytes.fromhex("55 00 00"))
        self.assertEqual(job.steps[2].data[3:51], bytes([0x40]) + bytes(47))
        self.assertEqual(job.steps[2].data[51:99], bytes([0x80]) + bytes(47))
        self.assertEqual(job.steps[2].data[-1], 0)

    def test_rejects_non_lx_width(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(pixels=[0] * 8, width=8, pixel_format=PixelFormat.BW1)

        with self.assertRaisesRegex(ValueError, "384px raster width"):
            PrinterProtocol(device).build_job(
                RasterSet.from_single(raster),
                is_text=False,
            )

    def test_manual_paper_motion_is_explicit_noop_until_modeled(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        protocol = PrinterProtocol(device)

        self.assertEqual(protocol.build_paper_motion("feed").payload, b"")
        self.assertEqual(protocol.build_paper_motion("retract").payload, b"")


class FunnyLxRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_handshake_uses_address_and_crc_challenge(self) -> None:
        random_bytes = bytes.fromhex("f7 cf 01 02 03 04 05 06 07 08")
        mac = bytes.fromhex("c00000000460")
        crc = challenge_crc(random_bytes, mac)
        session = _FunnyLxSession([
            bytes.fromhex("5a 01 00"),
            b"\x5A\x0A" + crc.low,
            bytes.fromhex("5a 0b 01"),
        ])
        controller = FunnyLxRuntimeController(
            bluetooth_address="C0:00:00:00:04:60",
            random_bytes_factory=lambda: random_bytes,
        )

        await controller.initialize_connection(session, mtu_size=508, timeout=0.1)

        self.assertEqual(session.packets, [
            bytes.fromhex("5a 01 00"),
            b"\x5A\x0A" + random_bytes,
            b"\x5A\x0B" + crc.high,
        ])
        self.assertTrue(controller.debug_snapshot()["verified"])

    async def test_runtime_can_take_mac_from_status_reply(self) -> None:
        random_bytes = bytes.fromhex("f7 cf 01 02 03 04 05 06 07 08")
        mac = bytes.fromhex("c00000000460")
        crc = challenge_crc(random_bytes, mac)
        session = _FunnyLxSession([
            bytes.fromhex("5a 01 00 03 c0 00 00 00 04 60"),
            b"\x5A\x0A" + crc.low,
            bytes.fromhex("5a 0b 01"),
        ])
        controller = FunnyLxRuntimeController(
            bluetooth_address="not-a-mac",
            random_bytes_factory=lambda: random_bytes,
        )

        await controller.initialize_connection(session, mtu_size=508, timeout=0.1)

        self.assertEqual(session.packets[1], b"\x5A\x0A" + random_bytes)

    async def test_runtime_prefers_status_mac_over_os_address(self) -> None:
        random_bytes = bytes.fromhex("f7 cf 01 02 03 04 05 06 07 08")
        status_mac = bytes.fromhex("c00000000460")
        crc = challenge_crc(random_bytes, status_mac)
        session = _FunnyLxSession([
            bytes.fromhex("5a 01 00 03 c0 00 00 00 04 60"),
            b"\x5A\x0A" + crc.low,
            bytes.fromhex("5a 0b 01"),
        ])
        controller = FunnyLxRuntimeController(
            bluetooth_address="AA:BB:CC:DD:EE:FF",
            random_bytes_factory=lambda: random_bytes,
        )

        await controller.initialize_connection(session, mtu_size=508, timeout=0.1)

        self.assertEqual(session.packets[1], b"\x5A\x0A" + random_bytes)
        self.assertIn("mac_source=status", session.debugs[-1])


if __name__ == "__main__":
    unittest.main()
