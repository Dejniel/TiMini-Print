from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, replace

from ...devices import PrinterCatalog, PrinterDevice
from ...devices.profiles import LevelProfile, ModeLevelProfile, SpeedProfile
from ...protocol.families.eleph_control import (
    DEVICE_INFO_QUERY, STATUS_QUERY, ElephDeviceInfo, ElephReplyDecoder, ElephStatus,
)
from ...protocol.family import ProtocolFamily
from ...protocol.steps import ProtocolReplyExpectation, ProtocolReplyMatcher, ProtocolStep
from ...protocol.types import ImageEncoding
from ..errors import PrinterNotReadyError
from ..step_execution import execute_protocol_step
from .base import PreparedPrinter, RuntimeController, RuntimeSessionApi


@dataclass
class _ElephSessionState:
    """One receive stream survives the selection of an ESC or TSPL runtime."""

    info: ElephDeviceInfo | None = None
    status: ElephStatus | None = None
    query_disabled_reason: str | None = None
    decoder: ElephReplyDecoder = field(default_factory=ElephReplyDecoder)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    querying: bool = False
    job_active: bool = False
    job_fault: ElephStatus | None = None
    reprint_requested: bool = False


class ElephRuntimeController(RuntimeController):
    """Shared JX session state; the prepared device owns its ESC or TSPL dialect."""

    def __init__(self, *, state: _ElephSessionState | None = None) -> None:
        self._state = state if state is not None else _ElephSessionState()

    async def prepare(
        self, device: PrinterDevice, session: RuntimeSessionApi, *, timeout: float,
    ) -> PreparedPrinter:
        reply = await self._query(
            session, DEVICE_INFO_QUERY, timeout=min(timeout, 5.0),
        )
        if reply is None:
            session.report_warning(
                short="Eleph information unavailable",
                detail="Using the selected profile without confirmed device settings.",
            )
            return PreparedPrinter(device, self)

        info = ElephDeviceInfo.parse(reply)
        if info.command_mode == 1:
            family = ProtocolFamily.ELEPH_ESC
            encoding = ImageEncoding.ELEPH_ESC_ZLIB if info.compression else ImageEncoding.ELEPH_ESC_RAW
        elif info.command_mode == 2:
            family = ProtocolFamily.ELEPH_TSPL
            encoding = ImageEncoding.ELEPH_TSPL_ZLIB if info.compression else ImageEncoding.ELEPH_TSPL_BITMAP
        else:
            raise ValueError(f"Eleph reported unknown command mode: {info.command_mode}")
        if info.width <= 0:
            raise ValueError("Eleph reported an invalid printable width")
        dpi = info.dpi
        baseline = PrinterCatalog.load().get_profile(device.profile_key)
        papers = []
        for paper in device.profile.paper_presets:
            # Replace catalog fallback geometry, not a caller's chosen label format.
            is_fallback = baseline is not None and paper == baseline.paper_preset(paper.key)
            width = info.width if is_fallback else paper.paper_width_px
            if width > info.width:
                raise ValueError("Selected Eleph paper exceeds the reported printable width")
            papers.append(replace(paper, paper_width_px=width,
                                  render_width_px=width - paper.left_padding_px))
        density = LevelProfile(info.density, info.density, info.density)
        profile = replace(
            device.profile,
            dev_dpi=dpi,
            paper_presets=tuple(papers),
            print_defaults=replace(
                device.profile.print_defaults,
                speed=SpeedProfile(info.speed, info.speed),
                density=ModeLevelProfile(density, density),
            ),
        )
        pipeline = replace(device.image_pipeline, encoding=encoding)
        selected = replace(device, protocol_family=family).with_print_profile(
            profile, image_pipeline=pipeline,
        )
        with self._state.lock:
            self._state.info = info
        # A family change publishes a prepared runtime, not its bootstrap instance.
        controller = self if family is device.protocol_family else ElephRuntimeController(state=self._state)
        session.report_debug(
            f"Eleph settings: width={info.width} dpi={dpi} mode={family.value} "
            f"speed={info.speed:02x} density={info.density} compression={info.compression} "
            f"paper_type={info.paper_type} version={info.version}"
        )
        return PreparedPrinter(selected, controller)

    async def _query(
        self, session: RuntimeSessionApi, packet: bytes, *, timeout: float,
    ) -> bytes | None:
        if self._state.query_disabled_reason is not None:
            return None
        if not (session.can_query_control_packet() or session.can_send_control_packet_wait_notification()):
            self._disable_queries(session, "connection has no request/reply support")
            return None

        def complete(data: bytes) -> bool:
            return self._state.decoder.query_reply(packet, data)[0] is not None

        step = ProtocolStep.query(
            "Eleph information" if packet == DEVICE_INFO_QUERY else "Eleph status", packet,
            expect=ProtocolReplyExpectation.NONE, include_in_payload=False,
            timeout_sec=max(0.0, timeout),
            reply_matcher=ProtocolReplyMatcher(complete),
        )
        with self._state.lock:
            self._state.querying = True
        try:
            try:
                reply = await execute_protocol_step(session, step, timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError, NotImplementedError):
                reply = None
        finally:
            with self._state.lock:
                self._state.querying = False
        record, notifications = self._state.decoder.query_reply(packet, reply or b"")
        if packet == STATUS_QUERY and record is not None:
            self._record_status(session, ElephStatus.parse(record))
        if notifications:
            self.handle_notification(session, notifications)
        if record is None:
            # Fixed records carry no request ID. A late JXIG must not become a
            # status reply (or vice versa); retry only on a fresh connection.
            self._disable_queries(session, "missing or incomplete reply")
        return record

    def _disable_queries(self, session: RuntimeSessionApi, reason: str) -> None:
        self._state.query_disabled_reason = reason
        session.report_debug(f"Eleph queries unavailable: {reason}; keeping send-only printing")

    def _record_status(self, session: RuntimeSessionApi, status: ElephStatus) -> None:
        with self._state.lock:
            self._state.status = status
            if self._state.job_active and status.error_codes:
                self._state.job_fault = status
        session.report_debug(
            f"Eleph status: raw={status.raw.hex(' ')} "
            f"errors={','.join(code.value for code in status.error_codes) or '<none>'}"
        )

    async def _check_status(self, session: RuntimeSessionApi, *, timeout: float) -> ElephStatus | None:
        reply = await self._query(session, STATUS_QUERY, timeout=timeout)
        if reply is None:
            return None
        return ElephStatus.parse(reply)

    @asynccontextmanager
    async def job_scope(self, session: RuntimeSessionApi, job, *, timeout: float):
        with self._state.lock:
            self._state.job_active = True
            self._state.job_fault = None
        try:
            await self._check_status(session, timeout=min(timeout, 0.5))
            self._raise_job_fault()
            yield
            self._raise_job_fault()
        finally:
            with self._state.lock:
                self._state.job_active = False

    def _raise_job_fault(self) -> None:
        with self._state.lock:
            fault = self._state.job_fault if self._state.job_active else None
        if fault is not None:
            raise PrinterNotReadyError(
                f"Eleph: {', '.join(code.value for code in fault.error_codes)}",
                *fault.error_codes,
            )

    async def before_write(self, session: RuntimeSessionApi, *, size: int, timeout: float) -> None:
        self._raise_job_fault()

    def handle_notification(self, session: RuntimeSessionApi, payload: bytes) -> None:
        with self._state.lock:
            # Owned fixed-length records may contain 'err:' in their text fields.
            # Decode them through the query, never scan their fragments as events.
            if self._state.querying:
                return
            frames = self._state.decoder.feed(payload)
        for frame in frames:
            if isinstance(frame, ElephStatus):
                self._record_status(session, frame)
            else:
                with self._state.lock:
                    self._state.reprint_requested = True
                session.report_warning(
                    short="Eleph reprint requested",
                    detail="Printer requested a reprint; TiMini will not replay the job automatically.",
                )

    def diagnostic_fields(self, device: PrinterDevice) -> tuple[str, ...]:
        return ("device_info", "status")

    async def query_diagnostic(
        self, device: PrinterDevice, session: RuntimeSessionApi, field: str, *, timeout: float,
    ) -> object | None:
        if field == "device_info":
            reply = await self._query(session, DEVICE_INFO_QUERY, timeout=timeout)
            if reply is None:
                return None
            # A diagnostic read does not mutate the already prepared print inputs.
            return asdict(ElephDeviceInfo.parse(reply))
        if field == "status":
            status = await self._check_status(session, timeout=timeout)
            return None if status is None else {
                "raw": status.raw.hex(), "errors": [code.value for code in status.error_codes],
            }
        raise ValueError(f"Unknown Eleph diagnostic field: {field}")

    def debug_snapshot(self) -> dict[str, object]:
        with self._state.lock:
            return {
                "device_info": None if self._state.info is None else asdict(self._state.info),
                "status": None if self._state.status is None else self._state.status.raw.hex(),
                "status_errors": [] if self._state.status is None else [
                    code.value for code in self._state.status.error_codes
                ],
                "query_disabled_reason": self._state.query_disabled_reason,
                "reprint_requested": self._state.reprint_requested,
            }
