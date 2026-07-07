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
        self.packet_timeouts: list[float] = []
        self.control_packets: list[bytes] = []
        self.control_timeouts: list[float] = []
        self.standard_payloads: list[bytes] = []
        self.wait_replies: list[bytes] = []
        self.debugs: list[str] = []
        self.warnings: list[tuple[str, str]] = []
        self.notify_started = True
        self.on_standard_payload = None

    def can_send_control_packet_wait_notification(self) -> bool:
        return True

    def can_send_control_packet(self) -> bool:
        return True

    def can_send_standard_payload(self) -> bool:
        return True

    def can_wait_for_notification(self) -> bool:
        return True

    def can_query_control_packet(self) -> bool:
        return False

    async def send_control_packet_wait_notification(
        self,
        packet: bytes,
        *,
        label: str,
        match,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        _ = label, required
        self.packets.append(bytes(packet))
        self.packet_timeouts.append(timeout)
        reply = self.replies.pop(0)
        if not match(reply):
            raise AssertionError(f"reply did not match: {reply.hex()}")
        return reply

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        self.control_packets.append(bytes(packet))
        self.control_timeouts.append(timeout)
        return True

    async def wait_for_notification(
        self,
        label: str,
        match,
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        _ = label, timeout, required
        while self.wait_replies:
            reply = self.wait_replies.pop(0)
            if match(reply):
                return reply
        if required:
            raise AssertionError("wait reply did not match any queued notification")
        return None

    async def send_standard_payload(self, data: bytes) -> None:
        payload = bytes(data)
        self.standard_payloads.append(payload)
        if self.on_standard_payload is not None:
            self.on_standard_payload(payload)

    def report_debug(self, message: str) -> None:
        self.debugs.append(message)

    def report_warning(self, *, short: str, detail: str) -> None:
        self.warnings.append((short, detail))


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
        names = (
            "LX-D01",
            "LX-D02",
            "LX-D2",
            *(f"LX-D{index}" for index in range(3, 10)),
            *(f"LX-D0{index}" for index in range(3, 10)),
        )
        for name in names:
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
            "image transfer ready",
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
        self.assertFalse(job.steps[3].reply_matcher.matches(bytes.fromhex("5a 08 00")))
        self.assertEqual(job.steps[3].timeout_sec, 10.0)
        self.assertEqual(job.steps[4].data, bytes.fromhex("5a 04 00 01 01"))
        self.assertTrue(job.steps[4].reply_matcher.matches(bytes.fromhex("5a 04 00 01 01")))
        self.assertEqual(job.steps[4].timeout_sec, 10.0)

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

    def test_manual_paper_motion_uses_known_feed_and_explicit_retract_guess(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        protocol = PrinterProtocol(device)

        self.assertEqual(protocol.build_paper_motion("feed").payload, bytes.fromhex("5a 03 82 00 04 00 00 00 00 00 00 00"))
        self.assertEqual(protocol.build_paper_motion("retract").payload, bytes.fromhex("5a 03 81 00 04 00 00 00 00 00 00 00"))


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
        self.assertEqual(session.packet_timeouts, [5.0, 5.0, 5.0])
        self.assertEqual(session.control_packets, [])
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
        self.assertEqual(session.control_packets, [bytes.fromhex("5a 0c 03")])
        self.assertEqual(session.control_timeouts, [0.1])
        self.assertEqual(controller.debug_snapshot()["darkness_code"], 3)
        self.assertTrue(controller.debug_snapshot()["supports_darkness"])

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

    async def test_runtime_skips_duplicate_default_darkness_step_after_handshake(self) -> None:
        random_bytes = bytes.fromhex("f7 cf 01 02 03 04 05 06 07 08")
        mac = bytes.fromhex("c00000000460")
        crc = challenge_crc(random_bytes, mac)
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(pixels=[0] * (384 * 2), width=384, pixel_format=PixelFormat.BW1)
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=4,
        )
        session = _FunnyLxSession([
            bytes.fromhex("5a 01 00 03 c0 00 00 00 04 60"),
            b"\x5A\x0A" + crc.low,
            bytes.fromhex("5a 0b 01"),
            bytes.fromhex("5a 04 00 01 01"),
        ])
        session.wait_replies = [bytes.fromhex("5a 06 00")]
        controller = FunnyLxRuntimeController(
            bluetooth_address="not-a-mac",
            random_bytes_factory=lambda: random_bytes,
        )

        await controller.initialize_connection(session, mtu_size=508, timeout=0.1)
        sent = await controller.send_protocol_steps(session, job.steps, timeout=0.1)

        self.assertTrue(sent)
        self.assertEqual(session.control_packets, [bytes.fromhex("5a 0c 03")])
        self.assertNotIn(bytes.fromhex("5a 0c 03"), session.standard_payloads)

    async def test_runtime_sends_non_default_darkness_step_after_handshake(self) -> None:
        random_bytes = bytes.fromhex("f7 cf 01 02 03 04 05 06 07 08")
        mac = bytes.fromhex("c00000000460")
        crc = challenge_crc(random_bytes, mac)
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(pixels=[0] * (384 * 2), width=384, pixel_format=PixelFormat.BW1)
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=5,
        )
        session = _FunnyLxSession([
            bytes.fromhex("5a 01 00 03 c0 00 00 00 04 60"),
            b"\x5A\x0A" + crc.low,
            bytes.fromhex("5a 0b 01"),
            bytes.fromhex("5a 04 00 01 01"),
        ])
        session.wait_replies = [bytes.fromhex("5a 06 00")]
        controller = FunnyLxRuntimeController(
            bluetooth_address="not-a-mac",
            random_bytes_factory=lambda: random_bytes,
        )

        await controller.initialize_connection(session, mtu_size=508, timeout=0.1)
        sent = await controller.send_protocol_steps(session, job.steps, timeout=0.1)

        self.assertTrue(sent)
        self.assertIn(bytes.fromhex("5a 0c 04"), session.standard_payloads)
        self.assertEqual(controller.debug_snapshot()["darkness_code"], 4)

    async def test_runtime_resends_from_previous_image_packet_on_5a05_retry(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(
            pixels=[0] * (384 * 4),
            width=384,
            pixel_format=PixelFormat.BW1,
        )
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )
        session = _FunnyLxSession([bytes.fromhex("5a 04 00 02 01")])
        session.wait_replies = [bytes.fromhex("5a 06 00")]
        controller = FunnyLxRuntimeController(bluetooth_address="C0:00:00:00:04:60")
        retry_sent = False

        def request_retry_after_first_packet(payload: bytes) -> None:
            nonlocal retry_sent
            if retry_sent or not payload.startswith(bytes.fromhex("55 00 00")):
                return
            retry_sent = True
            controller.handle_notification(session, bytes.fromhex("5a 05 00 01"))

        session.on_standard_payload = request_retry_after_first_packet

        sent = await controller.send_protocol_steps(session, job.steps, timeout=0.1)

        image_packets = [payload for payload in session.standard_payloads if payload.startswith(b"\x55")]
        self.assertTrue(sent)
        self.assertEqual([packet[1:3] for packet in image_packets], [
            bytes.fromhex("00 00"),
            bytes.fromhex("00 00"),
            bytes.fromhex("00 01"),
        ])
        self.assertEqual(session.packets, [bytes.fromhex("5a 04 00 02 01")])
        self.assertEqual(session.warnings, [])

    async def test_runtime_handles_5a05_retry_while_waiting_for_image_transfer_ready(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(
            pixels=[0] * (384 * 4),
            width=384,
            pixel_format=PixelFormat.BW1,
        )
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )
        session = _FunnyLxSession([bytes.fromhex("5a 04 00 02 01")])
        session.wait_replies = [
            bytes.fromhex("5a 05 00 01"),
            bytes.fromhex("5a 06 00"),
        ]
        controller = FunnyLxRuntimeController(bluetooth_address="C0:00:00:00:04:60")

        sent = await controller.send_protocol_steps(session, job.steps, timeout=0.1)

        image_packets = [payload for payload in session.standard_payloads if payload.startswith(b"\x55")]
        self.assertTrue(sent)
        self.assertEqual([packet[1:3] for packet in image_packets], [
            bytes.fromhex("00 00"),
            bytes.fromhex("00 01"),
            bytes.fromhex("00 00"),
            bytes.fromhex("00 01"),
        ])
        self.assertEqual(session.packets, [bytes.fromhex("5a 04 00 02 01")])
        self.assertEqual(session.warnings, [])

    async def test_runtime_waits_for_5a06_after_5a08_pause(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(
            pixels=[0] * (384 * 2),
            width=384,
            pixel_format=PixelFormat.BW1,
        )
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )
        session = _FunnyLxSession([bytes.fromhex("5a 04 00 01 01")])
        session.wait_replies = [
            bytes.fromhex("5a 08 00"),
            bytes.fromhex("5a 06 00"),
        ]
        sleeps: list[float] = []

        async def record_sleep(delay: float) -> None:
            sleeps.append(delay)

        controller = FunnyLxRuntimeController(
            bluetooth_address="C0:00:00:00:04:60",
            sleep=record_sleep,
        )

        sent = await controller.send_protocol_steps(session, job.steps, timeout=0.1)

        self.assertTrue(sent)
        self.assertEqual(sleeps, [0.02])
        self.assertEqual(session.packets, [bytes.fromhex("5a 04 00 01 01")])
        self.assertEqual(session.warnings, [])

    async def test_runtime_does_not_send_footer_without_5a06_image_ready(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(
            pixels=[0] * (384 * 2),
            width=384,
            pixel_format=PixelFormat.BW1,
        )
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )
        session = _FunnyLxSession([bytes.fromhex("5a 04 00 01 01")])
        session.wait_replies = [bytes.fromhex("5a 08 00")]
        controller = FunnyLxRuntimeController(bluetooth_address="C0:00:00:00:04:60")

        with self.assertRaisesRegex(RuntimeError, "image transfer did not reach"):
            await controller.send_protocol_steps(session, job.steps, timeout=0.1)

        self.assertEqual(session.packets, [])
        self.assertEqual(len(session.warnings), 1)

    async def test_runtime_applies_5a07_packet_delay_hint_to_next_image_packets(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(
            pixels=[0] * (384 * 6),
            width=384,
            pixel_format=PixelFormat.BW1,
        )
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )
        session = _FunnyLxSession([bytes.fromhex("5a 04 00 03 01")])
        session.wait_replies = [bytes.fromhex("5a 06 00")]
        sleeps: list[float] = []

        async def record_sleep(delay: float) -> None:
            sleeps.append(delay)

        controller = FunnyLxRuntimeController(
            bluetooth_address="C0:00:00:00:04:60",
            sleep=record_sleep,
        )
        delay_sent = False

        def set_delay_after_first_packet(payload: bytes) -> None:
            nonlocal delay_sent
            if delay_sent or not payload.startswith(bytes.fromhex("55 00 00")):
                return
            delay_sent = True
            controller.handle_notification(session, bytes.fromhex("5a 07 28"))

        session.on_standard_payload = set_delay_after_first_packet

        sent = await controller.send_protocol_steps(session, job.steps, timeout=0.1)

        image_packets = [payload for payload in session.standard_payloads if payload.startswith(b"\x55")]
        self.assertTrue(sent)
        self.assertEqual([packet[1:3] for packet in image_packets], [
            bytes.fromhex("00 00"),
            bytes.fromhex("00 01"),
            bytes.fromhex("00 02"),
        ])
        self.assertEqual(sleeps, [0.02, 0.04, 0.04])
        self.assertEqual(controller.debug_snapshot()["packet_delay_hint_sec"], 0.04)
        self.assertEqual(session.warnings, [])

    async def test_runtime_stores_5a07_packet_delay_hint_as_seconds(self) -> None:
        session = _FunnyLxSession([])
        controller = FunnyLxRuntimeController(bluetooth_address="C0:00:00:00:04:60")

        controller.handle_notification(session, bytes.fromhex("5a 07 ff"))

        self.assertEqual(controller.debug_snapshot()["packet_delay_hint_sec"], 0.255)

    async def test_runtime_ignores_5a07_while_waiting_for_image_transfer_ready(self) -> None:
        device = PrinterCatalog.load().device_from_model("funny_lx_d")
        raster = RasterBuffer(
            pixels=[0] * (384 * 4),
            width=384,
            pixel_format=PixelFormat.BW1,
        )
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
        )
        session = _FunnyLxSession([bytes.fromhex("5a 04 00 02 01")])
        session.wait_replies = [
            bytes.fromhex("5a 07 14"),
            bytes.fromhex("5a 06 00"),
        ]
        controller = FunnyLxRuntimeController(bluetooth_address="C0:00:00:00:04:60")

        sent = await controller.send_protocol_steps(session, job.steps, timeout=0.1)

        image_packets = [payload for payload in session.standard_payloads if payload.startswith(b"\x55")]
        self.assertTrue(sent)
        self.assertEqual([packet[1:3] for packet in image_packets], [
            bytes.fromhex("00 00"),
            bytes.fromhex("00 01"),
        ])
        self.assertEqual(controller.debug_snapshot()["packet_delay_hint_sec"], 0.02)
        self.assertEqual(session.warnings, [])


if __name__ == "__main__":
    unittest.main()
