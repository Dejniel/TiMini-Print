import asyncio

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.runtime.niimbot import NiimbotRuntimeController
from timiniprint.protocol import ProtocolJob, ProtocolStep
from timiniprint.protocol.families.niimbot.core import (
    NiimbotResponse, connect_result_from_reply, frame, heartbeat_query, heartbeat_status_from_reply, parse_packets,
)


@pytest.mark.parametrize("version,model,command,data,reply", [
    (0, 768, 0xDC, b"\x01", 0xDD),
    (1, 768, 0xDC, b"\x01", 0xDD),
    (2, 768, 0xDC, b"\x01", 0xDD),
    (None, None, 0xDC, b"\x01", 0xDD),
    (0, 256, 0xC1, b"\x01", 0xC2),
    (2, 262, 0xC1, b"\x01", 0xC2),
    (3, 256, 0xDC, b"\x04", 0xD9),
    (3, 768, 0xDC, b"\x04", 0xD9),
    (4, 768, 0xDC, b"\x04", 0xD9),
    (5, 768, 0xDC, b"\x04", 0xD9),
])
def test_heartbeat_wire_recipe(version, model, command, data, reply):
    step = heartbeat_query(protocol_version=version, effective_model_id=model)
    packet = parse_packets(step.data[1:] if step.data[:1] == b"\x03" else step.data)[0]
    assert (packet.command, packet.data) == (command, data)
    assert step.reply_matcher.matches(frame(reply, b"\x01"))
    assert not step.reply_matcher.matches(frame(NiimbotResponse.PRINT_ERROR, b"\x01"))
    assert not step.reply_required and not step.include_in_payload


@pytest.mark.parametrize("model", [256, 257, 258, 260, 262])
def test_connect_heartbeat_accepts_free_busy_without_accepting_it_as_initial_connection(model):
    step = heartbeat_query(protocol_version=0, effective_model_id=model)
    for state in (0, 1):
        reply = frame(0xC4, bytes([state]))
        assert step.reply_matcher.matches(reply)
        assert connect_result_from_reply(reply) is None


def test_heartbeat_reply_layouts_use_payload_offsets_and_effective_model():
    raw = frame(0xDD, bytes(range(13)))
    expected = {"cover": 9, "battery_level": 10, "paper": 11, "paper_rfid": 12}
    assert heartbeat_status_from_reply(raw, effective_model_id=768) == expected
    assert heartbeat_status_from_reply(raw, effective_model_id=775) == expected
    assert heartbeat_status_from_reply(raw, effective_model_id=785) == {}
    advanced = frame(0xD9, bytes(range(9)))
    assert heartbeat_status_from_reply(advanced, effective_model_id=785) == {
        "battery_level": 2, "temperature": 3, "cover": 4, "paper": 5,
        "paper_rfid": 6, "ribbon_rfid": 7, "ribbon_state": 8,
    }
    assert heartbeat_status_from_reply(frame(0xDD, bytes(12)), effective_model_id=768) == {}
    assert heartbeat_status_from_reply(frame(0xD9, bytes(8)), effective_model_id=768) == {}
    assert heartbeat_status_from_reply(frame(0xDB, b"\x02"), effective_model_id=768) == {}


class HeartbeatConnection:
    def __init__(self, *, ble=False, model=768, version=0):
        self.ble, self.model, self.version = ble, model, version
        self.controller = None
        self.queries, self.sent = [], []
        self.disconnected = False
        self.heartbeat_seen = asyncio.Event()
        self.heartbeat_gate = asyncio.Event()
        self.heartbeat_gate.set()
        self.print_seen = asyncio.Event()
        self.print_gate = asyncio.Event()
        self.print_gate.set()
        self.heartbeat_reply = True
        self.heartbeat_packet = None
        self.heartbeat_timeouts = []
        self.send_error = None

    async def connect(self, _device): return self
    async def attach_runtime_controller(self, controller, *, timeout):
        if self.controller is not None and self.controller is not controller:
            await self.controller.stop(self)
        self.controller = controller
    def can_query_control_packet(self): return not self.ble
    def can_send_control_packet_wait_notification(self): return self.ble
    def can_wait_for_notification(self): return self.ble

    async def query_control_packet(self, packet, *, timeout, reply_complete):
        self.queries.append(packet)
        item = parse_packets(packet[1:] if packet[:1] == b"\x03" else packet)[0]
        if item.command == 0xDC or (item.command == 0xC1 and len(self.queries) > 1):
            self.heartbeat_timeouts.append(timeout)
            self.heartbeat_seen.set()
            await self.heartbeat_gate.wait()
            if not self.heartbeat_reply:
                return None
        response = {
            0xC1: frame(0xC2, bytes([self.version + 1 if self.version < 2 else 3])),
            0x40: frame(0x48, self.model.to_bytes(2, "big")),
            0xA5: frame(0xB5, bytes(11) + {2: b"\x02\x03", 3: b"\x02\x04",
                                         4: b"\x03\x00", 5: b"\x03\x02"}.get(self.version, b"\0\0")),
            0xDC: frame(0xD9 if item.data == b"\x04" else 0xDD, b"\x01"),
            0xF3: frame(0xF4, b"\x01"),
        }[item.command]
        if item.command == 0xDC and self.heartbeat_packet is not None:
            response = self.heartbeat_packet
        for offset in range(0, len(response), 2):
            if self.controller is not None:
                self.controller.handle_notification(self, response[offset:offset + 2])
        return response

    async def send_control_packet_wait_notification(self, packet, *, label, match, timeout, required):
        response = await self.query_control_packet(packet, timeout=timeout, reply_complete=match)
        for offset in range(0, len(response or b""), 2):
            fragment = response[offset:offset + 2]
            if match(fragment):
                return fragment
        return None

    async def send_standard_payload(self, data):
        self.print_seen.set()
        await self.print_gate.wait()
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    async def send(self, job):
        await self.send_standard_payload(job.payload)

    async def disconnect(self):
        await self.controller.stop(self)
        self.disconnected = True


def fast_heartbeat(monkeypatch):
    monkeypatch.setattr("timiniprint.printing.runtime.niimbot_heartbeat._HEARTBEAT_INTERVAL_SEC", .01)


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("version", [0, 3, 5])
def test_connected_reference_runtime_keeps_idle_session_and_stops_on_disconnect(monkeypatch, ble, version):
    fast_heartbeat(monkeypatch)
    async def run():
        device = PrinterCatalog.load().device_from_profile("niimbot_d110").with_protocol_variant("d110")
        link = HeartbeatConnection(ble=ble, version=version)
        controller = NiimbotRuntimeController()
        printer = await connect_printer(device, link, controller=controller)
        assert controller.debug_snapshot()["heartbeat_active"]
        await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
        await asyncio.sleep(.005)
        assert controller.debug_snapshot()["heartbeat_missed_replies"] == 0
        await printer.disconnect()
        count = len(link.queries)
        await asyncio.sleep(.03)
        assert len(link.queries) == count and link.disconnected
        assert not controller.debug_snapshot()["heartbeat_active"]
    asyncio.run(run())


@pytest.mark.parametrize("steps", [False, True])
@pytest.mark.parametrize("failure", [None, "error", "cancel"])
def test_heartbeat_excludes_print_and_resumes_afterwards(monkeypatch, steps, failure):
    fast_heartbeat(monkeypatch)
    async def run():
        device = PrinterCatalog.load().device_from_profile("niimbot_d110").with_protocol_variant("d110")
        link = HeartbeatConnection()
        controller = NiimbotRuntimeController()
        async with await connect_printer(device, link, controller=controller) as printer:
            link.heartbeat_gate.clear()
            await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
            link.print_gate.clear()
            if failure == "error":
                link.send_error = RuntimeError("write failed")
            job = (ProtocolJob(steps=(ProtocolStep.send("print end", frame(0xF3)),)) if steps
                   else ProtocolJob(payload=frame(0xF3)))
            sending = asyncio.create_task(printer.send_job(job))
            await asyncio.sleep(.03)
            assert not link.print_seen.is_set()  # pending idle query must drain
            link.heartbeat_gate.set()
            await asyncio.wait_for(link.print_seen.wait(), 1)
            before = len(link.queries)
            await asyncio.sleep(.03)
            assert len(link.queries) == before
            link.heartbeat_seen.clear()
            if failure == "cancel":
                sending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await sending
            else:
                link.print_gate.set()
                if failure:
                    with pytest.raises(RuntimeError, match="write failed"):
                        await sending
                else:
                    await sending
            await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
            assert not link.disconnected
            if failure:
                assert frame(0xF3) in link.queries
    asyncio.run(run())


def test_missing_heartbeat_replies_do_not_close_or_stop_session(monkeypatch):
    fast_heartbeat(monkeypatch)
    async def run():
        device = PrinterCatalog.load().device_from_profile("niimbot_d110").with_protocol_variant("d110")
        link = HeartbeatConnection()
        controller = NiimbotRuntimeController()
        async with await connect_printer(device, link, controller=controller):
            link.heartbeat_reply = False
            await asyncio.sleep(.12)
            assert controller.debug_snapshot()["heartbeat_missed_replies"] >= 6
            assert controller.debug_snapshot()["heartbeat_active"]
            assert not link.disconnected
            link.heartbeat_reply = True
            link.heartbeat_seen.clear()
            await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
            await asyncio.sleep(.005)
            assert controller.debug_snapshot()["heartbeat_missed_replies"] == 0
    asyncio.run(run())


def test_stop_from_another_event_loop_drains_heartbeat(monkeypatch):
    fast_heartbeat(monkeypatch)
    async def run():
        device = PrinterCatalog.load().device_from_profile("niimbot_d110").with_protocol_variant("d110")
        link = HeartbeatConnection()
        controller = NiimbotRuntimeController()
        printer = await connect_printer(device, link, controller=controller)
        link.heartbeat_gate.clear()
        await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
        await asyncio.get_running_loop().run_in_executor(None, lambda: asyncio.run(controller.stop(link)))
        count = len(link.queries)
        await asyncio.sleep(.03)
        assert len(link.queries) == count
        assert not controller.debug_snapshot()["heartbeat_active"]
        await printer.disconnect()
    asyncio.run(run())


def test_heartbeat_waits_between_exchanges_not_on_a_fixed_clock(monkeypatch):
    monkeypatch.setattr("timiniprint.printing.runtime.niimbot_heartbeat._HEARTBEAT_INTERVAL_SEC", .05)
    async def run():
        device = PrinterCatalog.load().device_from_profile("niimbot_d110").with_protocol_variant("d110")
        link = HeartbeatConnection()
        link.heartbeat_gate.clear()
        controller = NiimbotRuntimeController()
        async with await connect_printer(device, link, controller=controller):
            await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
            assert link.heartbeat_timeouts == [.5]
            await asyncio.sleep(.08)  # first exchange outlasts the interval
            link.heartbeat_seen.clear()
            link.heartbeat_gate.set()
            await asyncio.sleep(.02)
            assert not link.heartbeat_seen.is_set()
            await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
            assert len(link.heartbeat_timeouts) == 2
    asyncio.run(run())


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("version", [0, 3])
def test_idle_status_and_interleaved_progress_do_not_become_disconnect_or_print_error(monkeypatch, ble, version):
    fast_heartbeat(monkeypatch)
    async def run():
        device = PrinterCatalog.load().device_from_profile("niimbot_d110").with_protocol_variant("d110")
        link = HeartbeatConnection(ble=ble, version=version)
        data = bytearray(13 if version == 0 else 9)
        data[10 if version == 0 else 2] = 3
        data[11 if version == 0 else 5] = 1
        link.heartbeat_packet = frame(0xE0, b"\0\x7f") + frame(0xDD if version == 0 else 0xD9, data)
        controller = NiimbotRuntimeController()
        async with await connect_printer(device, link, controller=controller) as printer:
            await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
            await asyncio.sleep(.005)
            status = controller.debug_snapshot()["heartbeat_status"]
            assert status["battery_level"] == 3 and status["paper"] == 1
            await printer.send_job(ProtocolJob(payload=frame(0xF3)))
            assert not link.disconnected
    asyncio.run(run())


@pytest.mark.parametrize("steps", [False, True])
def test_heartbeat_remains_suspended_through_completion_wait(monkeypatch, steps):
    fast_heartbeat(monkeypatch)

    class CompletionController(NiimbotRuntimeController):
        def __init__(self):
            super().__init__()
            self.waiting = asyncio.Event()
            self.finish = asyncio.Event()

        async def wait_for_completion(self, session, *, timeout):
            self.waiting.set()
            await self.finish.wait()

    async def run():
        device = PrinterCatalog.load().device_from_profile("niimbot_d110").with_protocol_variant("d110")
        link = HeartbeatConnection()
        controller = CompletionController()
        async with await connect_printer(device, link, controller=controller) as printer:
            job = (ProtocolJob(steps=(ProtocolStep.send("end", frame(0xF3)),), wait_for_completion=True)
                   if steps else ProtocolJob(payload=frame(0xF3), wait_for_completion=True))
            sending = asyncio.create_task(printer.send_job(job))
            try:
                await asyncio.wait_for(controller.waiting.wait(), 1)
                before = len(link.queries)
                await asyncio.sleep(.04)
                assert len(link.queries) == before
            finally:
                controller.finish.set()
                await sending
            link.heartbeat_seen.clear()
            await asyncio.wait_for(link.heartbeat_seen.wait(), 1)
    asyncio.run(run())


def test_desktop_ble_disconnect_drains_idle_query_before_closing_adapter():
    from tests.test_session_lifecycle import _device, _Gate, _Link
    from timiniprint.printing.runtime.base import PreparedPrinter

    class ReadyController(NiimbotRuntimeController):
        async def prepare(self, device, session, *, timeout):
            return PreparedPrinter(device, self)

    async def run():
        device = _device()
        link = _Link(device)
        controller = ReadyController()
        gate = _Gate()
        link.client.gate = gate
        printer = await connect_printer(device, link, controller=controller)
        await asyncio.wait_for(gate.started.wait(), 2)
        closing = asyncio.create_task(printer.disconnect())
        try:
            await asyncio.sleep(.03)
            assert not closing.done()
            assert not link.client.disconnected and not link.loop.is_closed()
            gate.release()
            await asyncio.wait_for(closing, 2)
            assert link.client.disconnected and link.loop.is_closed()
            assert not controller.debug_snapshot()["heartbeat_active"]
        finally:
            if not link.loop.is_closed():
                gate.release()
            await asyncio.gather(closing, return_exceptions=True)
            await controller.stop(link.connection)
            await link.connection.disconnect()
            if not link.loop.is_closed():
                await asyncio.get_running_loop().run_in_executor(None, link.socket.close)
    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_activation_drains_started_heartbeat_before_adapter_cleanup(cancel):
    from tests.test_session_lifecycle import _device, _Gate, _Link
    from timiniprint.printing.runtime.base import PreparedPrinter

    class FailingActivation(NiimbotRuntimeController):
        async def prepare(self, device, session, *, timeout):
            return PreparedPrinter(device, self)

        async def after_prepare(self, session, *, timeout):
            await super().after_prepare(session, timeout=timeout)
            await gate.started.wait()
            if cancel:
                await asyncio.Event().wait()
            raise ValueError("activation failed")

    async def run():
        nonlocal gate
        device = _device()
        link = _Link(device)
        gate = _Gate()
        link.client.gate = gate
        controller = FailingActivation()
        preparing = asyncio.create_task(connect_printer(device, link, controller=controller))
        try:
            await asyncio.wait_for(gate.started.wait(), 2)
            if cancel:
                preparing.cancel()
            await asyncio.sleep(.03)
            assert not preparing.done()
            assert not link.client.disconnected and not link.loop.is_closed()
            gate.release()
            with pytest.raises(asyncio.CancelledError if cancel else ValueError):
                await asyncio.wait_for(preparing, 2)
            assert link.client.disconnected and link.loop.is_closed()
            assert not controller.debug_snapshot()["heartbeat_active"]
        finally:
            if not link.loop.is_closed():
                gate.release()
            await asyncio.gather(preparing, return_exceptions=True)
            await controller.stop(link.connection)
            await link.connection.disconnect()
            if not link.loop.is_closed():
                await asyncio.get_running_loop().run_in_executor(None, link.socket.close)
    gate = None
    asyncio.run(run())


@pytest.mark.parametrize("observe,query", [(False, True), (True, False)])
def test_send_only_connection_does_not_start_idle_polling(observe, query):
    class SendOnly(HeartbeatConnection):
        attach_runtime_controller = HeartbeatConnection.attach_runtime_controller if observe else None
        def can_query_control_packet(self): return query
        def can_send_control_packet_wait_notification(self): return False
        async def disconnect(self):
            if self.controller is not None:
                await self.controller.stop(self)
            self.disconnected = True

    async def run():
        device = PrinterCatalog.load().device_from_profile("niimbot_d110").with_protocol_variant("d110")
        controller = NiimbotRuntimeController()
        link = SendOnly()
        async with await connect_printer(device, link, controller=controller):
            before = len(link.queries)
            await asyncio.sleep(.02)
            assert len(link.queries) == before
            assert not controller.debug_snapshot()["heartbeat_active"]
    asyncio.run(run())
