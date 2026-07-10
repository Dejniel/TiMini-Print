from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from .. import reporting
from ..protocol import ProtocolJob, ProtocolReplyExpectation, ProtocolStep, ProtocolStepOperation
from ..protocol.steps import reply_matches_expectation
from .runtime.base import PreparedRuntimeContext
from .runtime.factory import runtime_controller_for_device
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
    runtime_context: PreparedRuntimeContext = PreparedRuntimeContext(),
) -> None:
    """Send a prepared protocol job, executing named protocol steps when present."""
    session = RuntimeConnectionSession(device, connection, reporter=reporter)
    sent_via_steps = False
    controller = runtime_context.runtime_controller
    if controller is None and job.wait_for_completion:
        controller = runtime_controller_for_device(device)

    if controller is not None:
        await session.attach_runtime_controller(controller, timeout=timeout)

    if job.steps:
        if controller is not None:
            sent_via_steps = await controller.send_protocol_steps(session, job.steps, timeout=timeout)
        if not sent_via_steps and session.can_send_standard_payload():
            sent_via_steps = await _send_protocol_steps(session, job.steps, timeout=timeout)
        elif not sent_via_steps:
            session.report_warning(
                short="Protocol step send unavailable",
                detail=(
                    "This job includes named protocol steps, but the current connection cannot send "
                    "raw standard payload chunks. Falling back to stream-only send."
                ),
            )

    if not sent_via_steps:
        await connection.send(job)

    # The transport returns as soon as the bytes are written, but some printers
    # (e.g. V5X/MXW01) keep printing for several seconds afterwards. Give the
    # runtime controller a chance to wait for the device to finish before the
    # caller closes the connection, so we don't truncate the output.
    if controller is not None and job.wait_for_completion:
        await controller.wait_for_completion(session, timeout=timeout)


async def _send_protocol_steps(
    session: RuntimeConnectionSession,
    steps: tuple[ProtocolStep, ...],
    *,
    timeout: float,
) -> bool:
    if any(step.operation is ProtocolStepOperation.QUERY for step in steps):
        if (
            not session.can_query_control_packet()
            and not session.can_send_control_packet_wait_notification()
        ):
            session.report_warning(
                short="Protocol query unavailable",
                detail=(
                    "This job needs request/response protocol steps, but the current transport "
                    "cannot query replies or send BLE notification queries. Falling back to stream-only send."
                ),
            )
            return False
    if any(step.operation is ProtocolStepOperation.WAIT for step in steps):
        if not session.can_wait_for_notification():
            session.report_warning(
                short="Protocol wait unavailable",
                detail=(
                    "This job needs a protocol notification wait, but the current transport "
                    "cannot wait for BLE notifications. Falling back to stream-only send."
                ),
            )
            return False

    for step in steps:
        if step.operation is ProtocolStepOperation.SEND:
            session.report_debug(f"Protocol send {step.label}: {_packet_summary(step.data)}")
            await session.send_standard_payload(step.data)
            continue
        if step.operation is ProtocolStepOperation.WAIT:
            reply = await _execute_wait_step(session, step, timeout=timeout)
            if not _reply_matches_for(step, reply):
                session.report_warning(
                    short=f"Protocol {step.label} wait mismatch",
                    detail=(
                        f"Protocol wait {step.label!r} did not receive the expected notification, "
                        f"got {_hex_preview(reply)}. Continuing, but the printer may reject the job."
                    ),
                )
            continue

        reply = await _execute_query_step(session, step, timeout=timeout)
        if not _reply_matches_for(step, reply):
            session.report_warning(
                short=f"Protocol {step.label} reply mismatch",
                detail=(
                    f"Protocol step {step.label!r} expected {step.expect.value}, "
                    f"got {_hex_preview(reply)}. Continuing, but the printer may reject the job."
                ),
            )
    return True


async def _execute_wait_step(
    session: RuntimeConnectionSession,
    step: ProtocolStep,
    *,
    timeout: float,
) -> bytes | None:
    reply_complete = _reply_complete_for(step)
    if reply_complete is None:
        return None
    wait_timeout = timeout if step.timeout_sec is None else step.timeout_sec
    reply = await session.wait_for_notification(
        step.label,
        reply_complete,
        timeout=wait_timeout,
        required=False,
    )
    session.report_debug(
        f"Protocol wait {step.label}: rx={_hex_preview(reply)}"
    )
    return reply


async def _execute_query_step(
    session: RuntimeConnectionSession,
    step: ProtocolStep,
    *,
    timeout: float,
) -> bytes | None:
    if step.repeat_interval_sec is None:
        return await _query_once(session, step, timeout=timeout)

    budget = step.repeat_timeout_sec if step.repeat_timeout_sec is not None else timeout
    deadline = time.monotonic() + budget
    reply: bytes | None = None
    while True:
        # Clamp to the configured budget: `deadline - monotonic()` can float a hair
        # above `budget` (catastrophic cancellation at large monotonic() values,
        # seen on Windows), which would let a single attempt exceed the cap.
        remaining = min(budget, deadline - time.monotonic())
        if remaining <= 0:
            return reply
        reply = await _query_once(
            session,
            step,
            timeout=min(timeout, remaining),
            limit_to_call_timeout=True,
        )
        if _reply_matches_for(step, reply):
            return reply
        remaining = min(budget, deadline - time.monotonic())
        if remaining <= 0:
            return reply
        await asyncio.sleep(min(step.repeat_interval_sec, remaining))


async def _query_once(
    session: RuntimeConnectionSession,
    step: ProtocolStep,
    *,
    timeout: float,
    limit_to_call_timeout: bool = False,
) -> bytes | None:
    if step.timeout_sec is None:
        query_timeout = timeout
    elif limit_to_call_timeout:
        query_timeout = min(step.timeout_sec, timeout)
    else:
        query_timeout = step.timeout_sec
    reply_complete = _reply_complete_for(step)
    if session.can_query_control_packet():
        reply = await session.query_control_packet(
            step.data,
            timeout=query_timeout,
            reply_complete=reply_complete,
        )
    else:
        if reply_complete is None:
            session.report_debug(
                f"Protocol send {step.label}: {_packet_summary(step.data)}"
            )
            await session.send_standard_payload(step.data)
            reply = None
        else:
            reply = await session.send_control_packet_wait_notification(
                step.data,
                label=step.label,
                match=reply_complete,
                timeout=query_timeout,
                required=False,
            )
    session.report_debug(
        f"Protocol query {step.label}: tx={_hex_preview(step.data)} rx={_hex_preview(reply)}"
    )
    return reply


def _reply_complete_for(step: ProtocolStep):
    if step.reply_matcher is not None:
        return step.reply_matcher.complete
    if step.expect is ProtocolReplyExpectation.NONE:
        return None
    return lambda reply: reply_matches_expectation(step.expect, reply)


def _reply_matches_for(step: ProtocolStep, reply: bytes | None) -> bool:
    if step.reply_matcher is not None:
        if step.reply_matcher.matches is not None:
            return step.reply_matcher.matches(reply)
        return bool(reply and step.reply_matcher.complete(reply))
    return reply_matches_expectation(step.expect, reply)


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
