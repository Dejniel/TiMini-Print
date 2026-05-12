from __future__ import annotations

from typing import TYPE_CHECKING

from .. import reporting
from ..protocol import ProtocolJob, ProtocolReplyExpectation, ProtocolStep, ProtocolStepOperation
from ..protocol.steps import reply_matches_expectation
from .runtime.session import RuntimeConnectionSession

if TYPE_CHECKING:
    from ..devices import PrinterDevice
    from ..transport.base import PrinterConnection


async def send_prepared_job(
    device: PrinterDevice,
    connection: PrinterConnection,
    job: ProtocolJob,
    *,
    timeout: float = 1.0,
    reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
) -> None:
    """Send a prepared protocol job, executing named protocol steps when present."""
    if job.steps:
        session = RuntimeConnectionSession(device, connection, reporter=reporter)
        if session.can_send_standard_payload():
            if await _send_protocol_steps(session, job.steps, timeout=timeout):
                return
        else:
            session.report_warning(
                short="Protocol step send unavailable",
                detail=(
                    "This job includes named protocol steps, but the current connection cannot send "
                    "raw standard payload chunks. Falling back to stream-only send."
                ),
            )

    await connection.send(job)


async def _send_protocol_steps(
    session: RuntimeConnectionSession,
    steps: tuple[ProtocolStep, ...],
    *,
    timeout: float,
) -> bool:
    if any(step.operation is ProtocolStepOperation.QUERY for step in steps):
        if not session.can_query_control_packet():
            session.report_warning(
                short="Protocol query unavailable",
                detail=(
                    "This job needs request/response protocol steps, but the current transport "
                    "cannot query replies. Falling back to stream-only send."
                ),
            )
            return False

    for step in steps:
        if step.operation is ProtocolStepOperation.SEND:
            session.report_debug(f"Protocol send {step.label}: {_packet_summary(step.data)}")
            await session.send_standard_payload(step.data)
            continue

        query_timeout = step.timeout_sec if step.timeout_sec is not None else timeout
        reply = await session.query_control_packet(
            step.data,
            timeout=query_timeout,
            reply_complete=_reply_complete_for(step),
        )
        session.report_debug(
            f"Protocol query {step.label}: tx={_hex_preview(step.data)} rx={_hex_preview(reply)}"
        )
        if not reply_matches_expectation(step.expect, reply):
            session.report_warning(
                short=f"Protocol {step.label} reply mismatch",
                detail=(
                    f"Protocol step {step.label!r} expected {step.expect.value}, "
                    f"got {_hex_preview(reply)}. Continuing, but the printer may reject the job."
                ),
            )
    return True


def _reply_complete_for(step: ProtocolStep):
    if step.expect is ProtocolReplyExpectation.NONE:
        return None
    return lambda reply: reply_matches_expectation(step.expect, reply)


def _packet_summary(data: bytes) -> str:
    if len(data) <= 32:
        return f"bytes={len(data)} data={data.hex(' ')}"
    return f"bytes={len(data)} head={data[:16].hex(' ')} tail={data[-16:].hex(' ')}"


def _hex_preview(data: bytes | None) -> str:
    if data is None:
        return "<none>"
    if not data:
        return "<empty>"
    if len(data) <= 32:
        return data.hex(" ")
    return f"{data[:16].hex(' ')} ... {data[-16:].hex(' ')} ({len(data)} bytes)"
