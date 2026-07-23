from __future__ import annotations

import asyncio
import time

from ..protocol import ProtocolReplyExpectation, ProtocolStep, ProtocolStepOperation
from ..protocol.steps import reply_matches_expectation
from .runtime.base import RuntimeSessionApi


async def execute_protocol_step(
    session: RuntimeSessionApi,
    step: ProtocolStep,
    *,
    timeout: float,
    log_prefix: str = "Protocol",
) -> bytes | None:
    if step.operation is ProtocolStepOperation.SEND:
        session.report_debug(f"{log_prefix} send {step.label}: {packet_summary(step.data)}")
        await session.send_standard_payload(step.data)
        return None
    if step.operation is ProtocolStepOperation.WAIT:
        return await _execute_wait_step(
            session,
            step,
            timeout=timeout,
            log_prefix=log_prefix,
        )
    if step.operation is ProtocolStepOperation.QUERY:
        return await _execute_query_step(
            session,
            step,
            timeout=timeout,
            log_prefix=log_prefix,
        )
    raise ValueError(f"Unsupported protocol step operation: {step.operation.value}")


async def _execute_wait_step(
    session: RuntimeSessionApi,
    step: ProtocolStep,
    *,
    timeout: float,
    log_prefix: str,
) -> bytes | None:
    reply_complete = reply_complete_for(step)
    if reply_complete is None:
        return None
    wait_timeout = timeout if step.timeout_sec is None else step.timeout_sec
    reply = await session.wait_for_reply(
        step.label,
        reply_complete,
        timeout=wait_timeout,
        required=False,
    )
    session.report_debug(f"{log_prefix} wait {step.label}: rx={bytes_preview(reply)}")
    return reply


async def _execute_query_step(
    session: RuntimeSessionApi,
    step: ProtocolStep,
    *,
    timeout: float,
    log_prefix: str,
) -> bytes | None:
    if step.repeat_interval_sec is None:
        return await _query_once(
            session,
            step,
            timeout=timeout,
            log_prefix=log_prefix,
        )

    budget = step.repeat_timeout_sec if step.repeat_timeout_sec is not None else timeout
    deadline = time.monotonic() + budget
    reply: bytes | None = None
    while True:
        remaining = min(budget, deadline - time.monotonic())
        if remaining <= 0:
            return reply
        reply = await _query_once(
            session,
            step,
            timeout=min(timeout, remaining),
            limit_to_call_timeout=True,
            log_prefix=log_prefix,
        )
        if reply_matches_for(step, reply):
            return reply
        remaining = min(budget, deadline - time.monotonic())
        if remaining <= 0:
            return reply
        await asyncio.sleep(min(step.repeat_interval_sec, remaining))


async def _query_once(
    session: RuntimeSessionApi,
    step: ProtocolStep,
    *,
    timeout: float,
    log_prefix: str,
    limit_to_call_timeout: bool = False,
) -> bytes | None:
    if step.timeout_sec is None:
        query_timeout = timeout
    elif limit_to_call_timeout:
        query_timeout = min(step.timeout_sec, timeout)
    else:
        query_timeout = step.timeout_sec
    reply_complete = reply_complete_for(step)
    if session.can_query_control_packet():
        reply = await session.query_control_packet(
            step.data,
            timeout=query_timeout,
            reply_complete=reply_complete,
        )
    elif reply_complete is None:
        session.report_debug(f"{log_prefix} send {step.label}: {packet_summary(step.data)}")
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
        f"{log_prefix} query {step.label}: "
        f"tx={bytes_preview(step.data)} rx={bytes_preview(reply)}"
    )
    return reply


def reply_complete_for(step: ProtocolStep):
    if step.reply_matcher is not None:
        return step.reply_matcher.complete
    if step.expect is ProtocolReplyExpectation.NONE:
        return None
    return lambda reply: reply_matches_expectation(step.expect, reply)


def reply_matches_for(step: ProtocolStep, reply: bytes | None) -> bool:
    if step.reply_matcher is not None:
        if step.reply_matcher.matches is not None:
            return step.reply_matcher.matches(reply)
        return bool(reply and step.reply_matcher.complete(reply))
    return reply_matches_expectation(step.expect, reply)


def packet_summary(data: bytes) -> str:
    if len(data) <= 32:
        return f"bytes={len(data)} data={data.hex(' ')}"
    return f"bytes={len(data)} head={data[:16].hex(' ')} tail={data[-16:].hex(' ')}"


def bytes_preview(data: bytes | None) -> str:
    if data is None:
        return "<none>"
    if not data:
        return "<empty>"
    if len(data) <= 32:
        return data.hex(" ")
    return f"{data[:16].hex(' ')} ... {data[-16:].hex(' ')} ({len(data)} bytes)"
