from __future__ import annotations

import asyncio
import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.runtime.niimbot import NiimbotRuntimeController
from timiniprint.protocol import PrinterProtocol
from timiniprint.protocol.families.niimbot.core import (
    NiimbotResponse,
    connect_packet,
    connect_result_from_reply,
    frame,
    model_id_query_packet,
    status_data_query_packet,
)
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class ProbeSession:
    def __init__(self, replies, *, notification_only=False):
        self.replies = dict(replies)
        self.notification_only = notification_only
        self.queries = []
        self.warnings = []

    def can_query_control_packet(self):
        return not self.notification_only

    def can_send_control_packet_wait_notification(self):
        return True

    async def query_control_packet(self, packet, *, timeout, reply_complete):
        self.queries.append(packet)
        reply = self.replies.get(packet)
        if reply is not None:
            assert reply_complete(reply)
        return reply

    async def send_control_packet_wait_notification(
        self, packet, *, label, match, timeout, required,
    ):
        assert not required
        return await self.query_control_packet(
            packet, timeout=timeout, reply_complete=match,
        )

    def report_debug(self, message):
        pass

    def report_warning(self, *, short, detail):
        self.warnings.append((short, detail))


class NiimbotRuntimeTests(unittest.TestCase):
    def test_probe_derives_version_from_connect_before_status(self):
        versions = (
            (1, b"\x03\x00", 0),
            (2, b"\x03\x00", 1),
            (3, b"\x02\x03", 0),
            (3, b"\x02\x04", 3),
            (3, b"\x02\x63", 3),
            (3, b"\x03\x00", 4),
            (3, b"\x03\x01", 4),
            (3, b"\x03\x02", 5),
            (3, None, None),
        )
        for notification_only in (False, True):
            for connect_result, status_version, expected in versions:
                with self.subTest(
                    notification_only=notification_only,
                    result=connect_result,
                    status=status_version,
                ):
                    self._check_probe(
                        notification_only, connect_result, status_version, expected,
                    )

    def _check_probe(self, notification_only, connect_result, status_version, expected):
        session = ProbeSession(
            {
                connect_packet(): frame(NiimbotResponse.CONNECT, bytes([connect_result])),
                status_data_query_packet(): (
                    frame(NiimbotResponse.PRINTER_STATUS_DATA, bytes(11) + status_version)
                    if status_version is not None else None
                ),
                model_id_query_packet(): frame(
                    NiimbotResponse.PRINTER_INFO_MODEL_ID, b"\x02\x00",
                ),
            },
            notification_only=notification_only,
        )
        controller = NiimbotRuntimeController()
        prepared = asyncio.run(controller.prepare(PrinterCatalog.load().detect_device("D11"), session, timeout=0.1))
        snapshot = controller.debug_snapshot()
        assert snapshot["connect_result"] == connect_result
        assert snapshot["protocol_version"] == expected
        assert snapshot["model_id"] == 512
        assert session.queries == (
            [connect_packet(), status_data_query_packet(), model_id_query_packet()]
            if connect_result == 3 else [connect_packet(), model_id_query_packet()]
        )

    def test_connect_parser_does_not_invent_a_result(self):
        for reply in (
            None, b"", b"invalid",
            frame(NiimbotResponse.PRINTER_STATUS_DATA),
            frame(NiimbotResponse.CONNECT, b""),
            frame(NiimbotResponse.CONNECT, b"\xff"),
        ):
            with self.subTest(reply=reply):
                self.assertIsNone(connect_result_from_reply(reply))

    def test_failed_reprobe_drops_previous_capabilities(self):
        for reply in (None, b"\x00", b"\x5a", b"\xff", b""):
            with self.subTest(reply=reply):
                self._check_failed_reprobe(reply)

    def _check_failed_reprobe(self, reply):
        session = ProbeSession({
            connect_packet(): frame(NiimbotResponse.CONNECT, b"\x02"),
            model_id_query_packet(): frame(NiimbotResponse.PRINTER_INFO_MODEL_ID, b"\x02\x00"),
        })
        controller = NiimbotRuntimeController()
        prepared = asyncio.run(controller.prepare(PrinterCatalog.load().detect_device("D11"), session, timeout=0.1))
        session.replies[connect_packet()] = (
            frame(NiimbotResponse.CONNECT, reply) if reply is not None else None
        )
        session.queries.clear()
        prepared = asyncio.run(controller.prepare(PrinterCatalog.load().detect_device("D11"), session, timeout=0.1))
        assert controller.debug_snapshot()["protocol_version"] is None
        assert controller.debug_snapshot()["model_id"] is None
        assert session.queries == [connect_packet()]
        assert session.warnings

    def test_d11_negotiates_new_task_without_changing_d11s(self):
        for name in ("D11", "D11S"):
            for connect_result in (None, 1, 2, 3):
                with self.subTest(name=name, connect_result=connect_result):
                    self._check_d11_task(name, connect_result)

    def _check_d11_task(self, name, connect_result):
        device = PrinterCatalog.load().detect_device(name)
        assert device is not None
        controller = runtime_controller_for_device(device)
        assert controller is not None
        session = ProbeSession({
            connect_packet(): (
                frame(NiimbotResponse.CONNECT, bytes([connect_result]))
                if connect_result is not None else None
            ),
            status_data_query_packet(): frame(
                NiimbotResponse.PRINTER_STATUS_DATA, bytes(11) + b"\x03\x00",
            ),
            model_id_query_packet(): frame(
                NiimbotResponse.PRINTER_INFO_MODEL_ID,
                (514 if name == "D11S" else 512).to_bytes(2, "big"),
            ),
        })
        prepared = asyncio.run(controller.prepare(device, session, timeout=0.1))
        resolved = prepared.device
        expected = "d110" if name == "D11" and connect_result == 2 else "d11_v1"
        assert resolved.protocol_variant == expected
        assert resolved.profile.protocol_default.packets_type == expected
        assert resolved.profile.default_paper_preset.paper_width_px == 96
        assert resolved.profile.dev_dpi == 203

        raster = RasterSet.from_single(
            RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1),
        )
        job = PrinterProtocol(resolved).build_job(raster, is_text=False)
        labels = [step.label for step in job.steps]
        assert ("print status" in labels) == (expected == "d110")
        assert ("page index" in labels) == (expected == "d11_v1")

    def test_d11_auto_requires_runtime_before_job_building(self):
        device = PrinterCatalog.load().detect_device("D11")
        assert device is not None
        raster = RasterSet.from_single(
            RasterBuffer(pixels=[0] * 96, width=96, pixel_format=PixelFormat.BW1),
        )
        with self.assertRaisesRegex(ValueError, "live protocol-version probe"):
            PrinterProtocol(device).build_job(raster, is_text=False)
