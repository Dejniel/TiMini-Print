from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.errors import PrinterNotReadyError
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.v5c import V5CRuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ImageEncoding, PrinterProtocol, ProtocolJob
from timiniprint.protocol.families.v5c import V5CPrintCapabilities, V5C_QUERY_STATUS_PACKET
from timiniprint.protocol.packet import make_packet
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class _Connection:
    def __init__(self, controller, *, status=0):
        self.controller = controller
        self.status = status
        self.payloads = []

    async def attach_runtime_controller(self, controller, **kwargs):
        self.controller = controller

    def report_warning(self, **kwargs):
        pass

    def report_debug(self, message):
        pass

    async def send_standard_payload(self, data):
        self.payloads.append(data)
        self.controller.handle_notification(self, make_packet(0xA1, bytes([self.status]), "v5c"))

    async def send(self, job):
        await self.send_standard_payload(job.payload)


def test_v5c_mode_follows_encoding_not_text_and_uses_literal_start():
    device = PrinterCatalog.load().device_from_profile("ytb01")
    for fmt, encoding, mode in ((PixelFormat.BW1, ImageEncoding.V5C_A4, 1),
                                (PixelFormat.GRAY8, ImageEncoding.V5C_A5, 2)):
        rasters = RasterSet.from_single(RasterBuffer([0] * 384, 384, fmt))
        for is_text in (False, True):
            job = PrinterProtocol(device).build_job(rasters, is_text=is_text,
                image_encoding_override=encoding, pixel_format_override=fmt)
            assert job.payload[6:8] == bytes([2, mode])
            assert bytes.fromhex("5688a30001000101ff") in job.payload
            assert job.payload.endswith(V5C_QUERY_STATUS_PACKET)


@pytest.mark.parametrize("steps", [False, True])
@pytest.mark.parametrize("status", [0, 1, 2, 3, 4, 8])
def test_first_idle_completes_and_printer_faults_stop_both_send_paths(steps, status):
    async def run():
        controller = V5CRuntimeController()
        device = PrinterCatalog.load().device_from_profile("ytb01")
        connection = _Connection(controller, status=status)
        built = PrinterProtocol(device).build_job(
            RasterSet.from_single(RasterBuffer([0] * 384, 384)), is_text=False)
        job = built if steps else ProtocolJob(payload=built.payload, wait_for_completion=True)
        if status:
            with pytest.raises(PrinterNotReadyError):
                await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        else:
            await send_prepared_job(PreparedPrinter(device, controller), connection, job)
            assert connection.payloads == [built.payload]
            assert controller.debug_snapshot()["print_complete_seen"]
            async with controller.job_scope(connection, job, timeout=0.1):
                assert not controller.debug_snapshot()["print_complete_seen"]
    asyncio.run(run())


def test_height_reply_uses_payload_bytes_two_and_three_and_fallbacks():
    async def run():
        controller = V5CRuntimeController()
        connection = _Connection(controller)
        device = PrinterCatalog.load().device_from_profile("ytb01")
        prepared = await controller.prepare(device, connection, timeout=0.1)
        assert prepared.capabilities.max_gray_height == 800
        for payload in (b"\x20\x03", b"\x20\x03\x00\x00"):
            controller.handle_notification(connection, make_packet(0xAA, payload, "v5c"))
            assert controller.debug_snapshot()["max_print_height"] is None
        controller.handle_notification(connection, make_packet(0xAA, b"\xff\xff\x20\x03", "v5c"))
        prepared = await controller.prepare(device, connection, timeout=0.1)
        assert prepared.capabilities.height_limit(ImageEncoding.V5C_A4) == 2400
        assert prepared.capabilities.height_limit(ImageEncoding.V5C_A5) == 800
    asyncio.run(run())


@pytest.mark.parametrize("fmt, encoding, maximum", [
    (PixelFormat.BW1, ImageEncoding.V5C_A4, 6),
    (PixelFormat.GRAY8, ImageEncoding.V5C_A5, 2),
])
def test_runtime_height_is_enforced_without_rescaling(fmt, encoding, maximum):
    device = PrinterCatalog.load().device_from_profile("ytb01")
    raster = RasterSet.from_single(RasterBuffer([0] * 384 * (maximum + 1), 384, fmt))
    with pytest.raises(ValueError, match=f"{maximum}-row limit"):
        PrinterProtocol(device).build_job(raster, is_text=False,
            image_encoding_override=encoding, pixel_format_override=fmt,
            runtime_capabilities=V5CPrintCapabilities(max_gray_height=2))


def test_init_height_query_is_prearmed_and_optional():
    async def run():
        controller = V5CRuntimeController()
        reply = make_packet(0xAA, b"\x00\x00\x64\x00", "v5c")

        def receive(packet, **kwargs):
            assert not kwargs["match"](reply[:6])
            assert kwargs["match"](reply[6:])
            return reply

        session = SimpleNamespace(
            can_query_control_packet=lambda: False,
            can_send_control_packet_wait_notification=lambda: True,
            send_control_packet_wait_notification=AsyncMock(side_effect=receive),
            report_debug=lambda message: None,
        )
        with patch("timiniprint.printing.runtime.v5c.asyncio.sleep", new=AsyncMock()):
            await controller.initialize_connection(session, mtu_size=23, timeout=0.1)
        call = session.send_control_packet_wait_notification.call_args
        assert call.kwargs["match"](reply)
        assert not call.kwargs["required"]
        assert controller.debug_snapshot()["max_print_height"] == 100
    asyncio.run(run())


@pytest.mark.parametrize("missing", [False, True])
def test_height_probe_uses_available_query_api_and_preserves_fallback(missing):
    async def run():
        controller = V5CRuntimeController()
        reply = make_packet(0xA1, b"\x80", "v5c") + make_packet(0xAA, b"\x00\x00\x64\x00", "v5c")
        query = AsyncMock(side_effect=TimeoutError()) if missing else AsyncMock(return_value=reply)
        session = SimpleNamespace(can_query_control_packet=lambda: True, query_control_packet=query,
                                  report_debug=lambda message: None)
        with patch("timiniprint.printing.runtime.v5c.asyncio.sleep", new=AsyncMock()):
            await controller.initialize_connection(session, mtu_size=23, timeout=10)
        assert query.call_args.kwargs["timeout"] == 0.4
        if not missing:
            assert query.call_args.kwargs["reply_complete"](reply)
        prepared = await controller.prepare(PrinterCatalog.load().device_from_profile("ytb01"), session, timeout=1)
        assert prepared.capabilities.max_gray_height == (800 if missing else 100)
    asyncio.run(run())


def test_fault_is_latched_for_current_job_but_idle_can_clear_it_afterwards():
    async def run():
        controller = V5CRuntimeController()
        connection = _Connection(controller)
        job = ProtocolJob(payload=b"data")
        with pytest.raises(PrinterNotReadyError):
            async with controller.job_scope(connection, job, timeout=0.1):
                controller.handle_notification(connection, make_packet(0xA1, b"\x01", "v5c"))
                controller.handle_notification(connection, make_packet(0xA1, b"\x00", "v5c"))
                await controller.before_write(connection, size=1, timeout=0.1)
        await controller.before_write(connection, size=1, timeout=0.1)
        controller.handle_notification(connection, make_packet(0xA1, b"\x00", "v5c"))
        async with controller.job_scope(connection, job, timeout=0.1):
            pass
    asyncio.run(run())
