from __future__ import annotations

from typing import TYPE_CHECKING

from .. import reporting
from ..protocol import ProtocolJob
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
    """Send a job, letting runtime controllers interleave session queries when needed."""
    runtime_controller = job.runtime_controller
    if runtime_controller is None:
        await connection.send(job)
        return

    session = RuntimeConnectionSession(device, connection, reporter=reporter)
    segments = job.payload_segments or (job.payload,)
    handled_any = False
    for index, payload in enumerate(segments):
        if await runtime_controller.send_standard_job_payload(
            session,
            payload,
            timeout=timeout,
        ):
            handled_any = True
            continue
        if handled_any:
            remaining_segments = tuple(segments[index:])
            await connection.send(
                ProtocolJob(
                    payload=b"".join(remaining_segments),
                    runtime_controller=runtime_controller,
                    payload_segments=remaining_segments,
                )
            )
        else:
            await connection.send(job)
        return
