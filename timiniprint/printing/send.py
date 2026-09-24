from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from .. import reporting
from ..protocol import ProtocolJob
from .runtime.base import PreparedPrinter
from .runtime.session import RuntimeConnectionSession
from .step_execution import execute_protocol_steps

if TYPE_CHECKING:
    from ..transport.base import PrinterConnection


async def send_prepared_job(
    prepared: PreparedPrinter,
    connection: PrinterConnection,
    job: ProtocolJob,
    *,
    timeout: float = 1.0,
    reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
) -> None:
    """Send a prepared protocol job, executing named protocol steps when present."""
    session = RuntimeConnectionSession(connection, reporter=reporter)
    sent_via_steps = False
    controller = prepared.runtime_controller

    async with AsyncExitStack() as scope:
        if controller is not None:
            await scope.enter_async_context(controller.job_scope(session, job, timeout=timeout))

        if job.steps:
            if controller is not None:
                sent_via_steps = await controller.send_protocol_steps(session, job.steps, timeout=timeout)
            if not sent_via_steps and session.can_send_standard_payload():
                sent_via_steps = await execute_protocol_steps(session, job.steps, timeout=timeout)
            elif not sent_via_steps:
                session.report_warning(
                    short="Protocol step send unavailable",
                    detail=(
                        "This job includes named protocol steps, but the current connection cannot send "
                        "raw standard payload chunks. Falling back to stream-only send."
                    ),
                )

        if not sent_via_steps:
            if any(step.reply_required for step in job.steps):
                raise RuntimeError(
                    "This job requires protocol replies; stream-only sending cannot confirm them"
                )
            await connection.send(job)

        # The transport returns as soon as the bytes are written, but some printers
        # (e.g. V5X/MXW01) keep printing for several seconds afterwards. Give the
        # runtime controller a chance to wait for the device to finish before the
        # caller closes the connection, so we don't truncate the output.
        if controller is not None and job.wait_for_completion:
            await controller.wait_for_completion(session, timeout=timeout)
