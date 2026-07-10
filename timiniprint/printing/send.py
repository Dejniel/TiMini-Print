from __future__ import annotations

from typing import TYPE_CHECKING

from .. import reporting
from ..protocol import ProtocolJob, ProtocolStep, ProtocolStepOperation
from .runtime.base import PreparedRuntimeContext
from .runtime.factory import runtime_controller_for_device
from .runtime.session import RuntimeConnectionSession
from .step_execution import bytes_preview, execute_protocol_step, reply_matches_for

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
    session = RuntimeConnectionSession(connection, reporter=reporter)
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
        reply = await execute_protocol_step(session, step, timeout=timeout)
        if step.operation is ProtocolStepOperation.WAIT:
            if not reply_matches_for(step, reply):
                session.report_warning(
                    short=f"Protocol {step.label} wait mismatch",
                    detail=(
                        f"Protocol wait {step.label!r} did not receive the expected notification, "
                        f"got {bytes_preview(reply)}. Continuing, but the printer may reject the job."
                    ),
                )
            continue
        if step.operation is ProtocolStepOperation.QUERY and not reply_matches_for(step, reply):
            session.report_warning(
                short=f"Protocol {step.label} reply mismatch",
                detail=(
                    f"Protocol step {step.label!r} expected {step.expect.value}, "
                    f"got {bytes_preview(reply)}. Continuing, but the printer may reject the job."
                ),
            )
    return True
