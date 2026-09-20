from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING, Mapping

from ...protocol.families.niimbot.core import (
    NiimbotConnectResult,
    NiimbotResponse,
    NiimbotReplyDecoder,
    connect_packet,
    connect_result_from_reply,
    model_id_from_reply,
    model_id_query_packet,
    protocol_version_from_status_data,
    response_matcher,
    status_data_query_packet,
    frame,
)
from ...protocol import ProtocolStep, ProtocolStepOperation
from ...protocol.steps import ProtocolReplyExpectation
from ...protocol.status import PrinterStatusCode
from ..errors import PrinterNotReadyError
from ..step_execution import ProtocolReplyError, execute_protocol_step
from .base import PreparedPrinter, RuntimeController, RuntimeSessionApi

if TYPE_CHECKING:
    from ...devices import PrinterDevice


_PRINTER_FAULTS = {
    1: PrinterStatusCode.COVER_OPEN,
    2: PrinterStatusCode.PAPER_OUT,
    3: PrinterStatusCode.LOW_BATTERY,
    7: PrinterStatusCode.OVERHEATED,
    14: PrinterStatusCode.RIBBON_ERROR,
    17: PrinterStatusCode.RIBBON_ERROR,
    18: PrinterStatusCode.RIBBON_ERROR,
}


@dataclass
class _NiimbotProbeState:
    connect_result: NiimbotConnectResult | None = None
    model_id: int | None = None
    model_reply: bytes | None = None
    protocol_version: int | None = None
    warning_emitted: bool = False
    hardware_reply: bytes | None = None


class NiimbotRuntimeController(RuntimeController):
    def __init__(self) -> None:
        self._state = _NiimbotProbeState()
        self._reply_lock = threading.RLock()
        self._reply_decoder = NiimbotReplyDecoder()
        self._page_index = 0
        self._printing = False
        self._error = 0

    @asynccontextmanager
    async def job_scope(self, session, job, *, timeout):
        starts = any(step.data[:3] == b"\x55\x55\x01" for step in job.steps)
        with self._reply_lock:
            if starts:
                self._reply_decoder = NiimbotReplyDecoder()
                self._page_index = 0
                self._error = 0
            self._printing = True
        failed = True
        try:
            yield
            failed = False
        finally:
            if failed or any(step.data[:3] == b"\x55\x55\xf3" for step in job.steps):
                with self._reply_lock:
                    self._printing = False

    def handle_notification(self, session: RuntimeSessionApi, payload: bytes) -> None:
        with self._reply_lock:
            packets = self._reply_decoder.feed(payload)
            if self._printing:
                self._record_packets(packets)

    def _check_error(self) -> None:
        with self._reply_lock:
            code = self._error
        if code:
            raise PrinterNotReadyError(f"NIIMBOT reported printer error 0x{code:02x}",
                                       _PRINTER_FAULTS.get(code, PrinterStatusCode.PRINTER_ERROR))

    async def send_protocol_steps(self, session, steps, *, timeout):
        if not session.can_send_standard_payload():
            return False
        for step in steps:
            self._check_error()
            if step.operation is ProtocolStepOperation.WAIT and step.reply_matcher is not None:
                decoder = NiimbotReplyDecoder()

                def completed(payload):
                    with self._reply_lock:
                        self._record_packets(decoder.feed(payload))
                        cached = frame(NiimbotResponse.PRINTER_PAGE_INDEX, self._page_index.to_bytes(2, "big"))
                    return bool(self._error or step.reply_matcher.matches(cached))

                if not completed(b""):
                    reply = await session.wait_for_reply(step.label, completed,
                                                         timeout=step.timeout_sec or timeout,
                                                         required=False)
                    self._record_query_reply(reply)
                if step.reply_required and not completed(b""):
                    raise ProtocolReplyError(step, None)
            else:
                try:
                    reply = await execute_protocol_step(session, step, timeout=timeout, log_prefix="NIIMBOT")
                except ProtocolReplyError as exc:
                    self._record_query_reply(exc.reply)
                    self._check_error()
                    raise
                # Query implementations can return replies without observer delivery.
                self._record_query_reply(reply)
            self._check_error()
            if step.data[:3] == b"\x55\x55\xf3":
                with self._reply_lock:
                    self._printing = False
        return True

    def _record_query_reply(self, reply: bytes | None) -> None:
        with self._reply_lock:
            self._record_packets(NiimbotReplyDecoder().feed(reply or b""))

    def _record_packets(self, packets) -> None:
        for packet in packets:
            if packet.command == NiimbotResponse.PRINTER_PAGE_INDEX and len(packet.data) >= 2:
                self._page_index = max(self._page_index, int.from_bytes(packet.data[:2], "big"))
            if packet.command == NiimbotResponse.PRINT_ERROR and packet.data:
                self._error = packet.data[0] or self._error
            if packet.command == NiimbotResponse.PRINT_STATUS and len(packet.data) >= 7:
                self._error = packet.data[6] or self._error

    async def prepare(
        self,
        device: PrinterDevice,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> PreparedPrinter:
        await self._query_printer(session, timeout=timeout)
        return PreparedPrinter(device, self)

    async def _query_printer(self, session: RuntimeSessionApi, *, timeout: float) -> None:
        self._state = _NiimbotProbeState()
        connect_reply = await self._query(
            session,
            "connect",
            connect_packet(),
            NiimbotResponse.CONNECT,
            timeout=timeout,
        )
        result = connect_result_from_reply(connect_reply)
        self._state.connect_result = result
        if result not in {
            NiimbotConnectResult.CONNECTED,
            NiimbotConnectResult.CONNECTED_NEW,
            NiimbotConnectResult.CONNECTED_V3,
        }:
            self._warn_probe_unavailable(session, reason="missing or unsuccessful connect result")
            return

        model_reply = await self._query(
            session, "model id", model_id_query_packet(),
            NiimbotResponse.PRINTER_INFO_MODEL_ID, timeout=timeout,
        )
        self._state.model_id = model_id_from_reply(model_reply)
        self._state.model_reply = model_reply
        if result is NiimbotConnectResult.CONNECTED_V3:
            status_reply = await self._query(
                session,
                "status data",
                status_data_query_packet(),
                NiimbotResponse.PRINTER_STATUS_DATA,
                timeout=timeout,
            )
            self._state.hardware_reply = status_reply
            self._state.protocol_version = protocol_version_from_status_data(status_reply) or 2
        else:
            self._state.protocol_version = (
                1 if result is NiimbotConnectResult.CONNECTED_NEW else 0
            )

        session.report_debug(
            "NIIMBOT probe: "
            f"connect_result={result.name} "
            f"model_id={self._state.model_id if self._state.model_id is not None else '<unknown>'} "
            f"protocol_version={self._state.protocol_version if self._state.protocol_version is not None else '<unknown>'}"
        )

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "connect_result": (
                int(self._state.connect_result)
                if self._state.connect_result is not None else None
            ),
            "model_id": self._state.model_id,
            "protocol_version": self._state.protocol_version,
            "warning_emitted": self._state.warning_emitted,
        }

    async def _query(
        self,
        session: RuntimeSessionApi,
        label: str,
        packet: bytes,
        expected: NiimbotResponse,
        *,
        timeout: float,
    ) -> bytes | None:
        if not (session.can_query_control_packet() or session.can_send_control_packet_wait_notification()):
            return None
        return await execute_protocol_step(session, ProtocolStep.query(
            f"NIIMBOT {label}", packet, expect=ProtocolReplyExpectation.NONE,
            timeout_sec=timeout, reply_matcher=response_matcher(expected),
            include_in_payload=False,
        ), timeout=timeout)

    def _warn_probe_unavailable(self, session: RuntimeSessionApi, *, reason: str) -> None:
        if self._state.warning_emitted:
            return
        self._state.warning_emitted = True
        session.report_warning(
            short="NIIMBOT probe unavailable",
            detail=(
                "NIIMBOT live model probe failed "
                f"({reason}). Printing may still work for an explicit model profile, but "
                "auto task selection is limited in this session."
            ),
        )


class VersionedNiimbotRuntimeController(NiimbotRuntimeController):
    def __init__(self, *, variants: Mapping[int, str], fallback_variant: str) -> None:
        super().__init__()
        self._variants = dict(variants)
        self._fallback_variant = fallback_variant

    async def prepare(
        self,
        device: PrinterDevice,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> PreparedPrinter:
        await self._query_printer(session, timeout=timeout)
        variant = self._variants.get(self._state.protocol_version, self._fallback_variant)
        return PreparedPrinter(device.with_protocol_variant(variant), self)

    def debug_snapshot(self) -> dict[str, object]:
        snapshot = super().debug_snapshot()
        snapshot["resolved_variant"] = self._variants.get(
            self._state.protocol_version, self._fallback_variant
        )
        return snapshot
