from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.errors import PrinterNotReadyError
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.v5c import V5CRuntimeController
from timiniprint.printing.runtime.v5g import V5GRuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolJob, ProtocolStep
from timiniprint.protocol.families.v5c import V5C_NOTIFY_PAUSE, V5C_NOTIFY_RESUME
from timiniprint.protocol.packet import make_packet
from timiniprint.protocol.status import PrinterStatusCode


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


@pytest.mark.parametrize("family, opcode, status, controller_class, reason", [
    ("v5g", 0xA3, 0x01, V5GRuntimeController, PrinterStatusCode.PAPER_OUT),
    ("v5g", 0xA3, 0x09, V5GRuntimeController, PrinterStatusCode.PAPER_OUT),
    ("v5g", 0xA3, 0x04, V5GRuntimeController, PrinterStatusCode.OVERHEATED),
    ("v5g", 0xA3, 0x08, V5GRuntimeController, PrinterStatusCode.LOW_BATTERY),
    ("v5c", 0xA1, 0x01, V5CRuntimeController, PrinterStatusCode.PAPER_OUT),
])
@pytest.mark.parametrize("split", range(1, 9))
@pytest.mark.parametrize("steps", [False, True])
def test_fragmented_printer_fault_stops_both_send_paths(catalog, family, opcode, status, controller_class, reason, split, steps):
    async def run():
        controller = controller_class()
        reply = make_packet(opcode, bytes([status]), family)

        class Connection:
            def report_debug(self, message): pass
            def report_warning(self, **kwargs): pass

            async def send_standard_payload(self, payload):
                controller.handle_notification(self, reply[:split])
                controller.handle_notification(self, reply[split:])

            async def send(self, job):
                await self.send_standard_payload(job.payload)

        device = catalog.device_from_profile("ytb01" if family == "v5c" else "v5g_small_203")
        job = ProtocolJob(payload=b"raster", steps=(ProtocolStep.send("raster", b"raster"),) if steps else ())
        with pytest.raises(PrinterNotReadyError) as error:
            await send_prepared_job(PreparedPrinter(device, controller), Connection(), job)
        assert error.value.reasons == (reason,)

    asyncio.run(run())


@pytest.mark.parametrize("status, reason", [(0x01, PrinterStatusCode.PAPER_OUT),
                                           (0x09, PrinterStatusCode.PAPER_OUT),
                                           (0x04, PrinterStatusCode.OVERHEATED),
                                           (0x08, PrinterStatusCode.LOW_BATTERY)])
def test_v5g_fault_is_latched_until_normal_status_after_the_job(status, reason):
    async def run():
        controller = V5GRuntimeController()
        session = SimpleNamespace(report_debug=Mock(), report_warning=Mock())
        job = ProtocolJob(payload=b"raster")
        fault = make_packet(0xA3, bytes([status]), "v5g")
        normal = make_packet(0xA3, b"\x00", "v5g")
        with pytest.raises(PrinterNotReadyError) as error:
            async with controller.job_scope(session, job, timeout=0.1):
                controller.handle_notification(session, fault + fault + normal)
                await controller.before_write(session, size=1, timeout=0.1)
        assert error.value.reasons == (reason,)
        assert not controller.debug_snapshot()["printing"]
        assert controller.debug_snapshot()["last_complete_time"] == 0
        session.report_warning.assert_called_once()
        with pytest.raises(PrinterNotReadyError) as error:
            async with controller.job_scope(session, job, timeout=0.1):
                pytest.fail("faulted printer must not start a new job")
        assert error.value.reasons == (reason,)
        controller.handle_notification(session, normal)
        async with controller.job_scope(session, job, timeout=0.1):
            await controller.before_write(session, size=1, timeout=0.1)

    asyncio.run(run())


def test_v5g_temperature_and_d2_remain_density_inputs_not_blocking_faults():
    async def run():
        controller = V5GRuntimeController()
        session = SimpleNamespace(report_debug=Mock(), report_warning=Mock())
        async with controller.job_scope(session, ProtocolJob(payload=b"raster"), timeout=0.1):
            controller.handle_notification(session, make_packet(0xD3, b"\x55", "v5g")
                                           + make_packet(0xD2, b"\x01", "v5g"))
            await controller.before_write(session, size=1, timeout=0.1)
        snapshot = controller.debug_snapshot()
        assert snapshot["temperature_c"] == 85
        assert snapshot["d2_status"]
        session.report_warning.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("family, controller_class", [("v5g", V5GRuntimeController), ("v5c", V5CRuntimeController)])
def test_coalesced_notifications_preserve_flow_and_partial_next_frame(family, controller_class):
    controller = controller_class()
    session = SimpleNamespace(report_debug=Mock(), report_warning=Mock(), set_flow_paused=Mock())
    if family == "v5c":
        status = make_packet(0xA1, b"\x80", family)
        pause, resume = V5C_NOTIFY_PAUSE, V5C_NOTIFY_RESUME
        update = make_packet(0xAA, b"\x00\x00\x64\x00", family)
        field, expected = "max_print_height", 100
    else:
        status = make_packet(0xA3, b"\x00", family)
        pause = bytes.fromhex("5178ae0101001070ff")
        resume = bytes.fromhex("5178ae0101000000ff")
        update = make_packet(0xD3, b"\x1c", family)
        field, expected = "temperature_c", 28
    controller.handle_notification(session, status + pause + resume + update[:5])
    assert [call.args[0] for call in session.set_flow_paused.call_args_list] == [True, False]
    assert [call.kwargs["payload"] for call in session.set_flow_paused.call_args_list] == [pause, resume]
    controller.handle_notification(session, update[5:])
    assert controller.debug_snapshot()[field] == expected


def test_fragmented_v5c_idle_completes_the_current_job():
    async def run():
        controller = V5CRuntimeController()
        session = SimpleNamespace(can_observe_replies=lambda: True)
        reply = make_packet(0xA1, b"\x00", "v5c")
        async with controller.job_scope(session, ProtocolJob(payload=b"raster"), timeout=0.1):
            for byte in reply:
                controller.handle_notification(session, bytes([byte]))
            assert controller.debug_snapshot()["print_complete_seen"]
            await asyncio.wait_for(controller.wait_for_completion(session, timeout=0.1), timeout=0.1)

    asyncio.run(run())


@pytest.mark.parametrize("path", ["payload", "steps", "fallback"])
@pytest.mark.parametrize("failure", [None, "send", "completion", "cancel"])
def test_v5g_job_scope_owns_printing_and_success_timestamp(catalog, path, failure):
    async def run():
        class Controller(V5GRuntimeController):
            async def wait_for_completion(self, session, *, timeout):
                assert self.debug_snapshot()["printing"]
                assert self.debug_snapshot()["last_complete_time"] == 0
                if failure == "completion":
                    raise RuntimeError("completion failed")

        controller = Controller()

        async def send(payload):
            assert controller.debug_snapshot()["printing"]
            if failure == "send":
                raise OSError("send failed")
            if failure == "cancel":
                raise asyncio.CancelledError()

        class Connection:
            async def send(self, job):
                await send(job.payload)

        connection = Connection()
        if path != "fallback":
            connection.send_standard_payload = send
        job = ProtocolJob(payload=b"raster", wait_for_completion=True,
                          steps=(ProtocolStep.send("raster", b"raster"),) if path != "payload" else ())
        prepared = PreparedPrinter(catalog.device_from_profile("v5g_small_203"), controller)
        if failure:
            error = {"send": OSError, "completion": RuntimeError, "cancel": asyncio.CancelledError}[failure]
            with pytest.raises(error):
                await send_prepared_job(prepared, connection, job)
        else:
            await send_prepared_job(prepared, connection, job)
        snapshot = controller.debug_snapshot()
        assert not snapshot["printing"]
        assert (snapshot["last_complete_time"] > 0) == (failure is None)

    asyncio.run(run())
