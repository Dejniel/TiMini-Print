"""Connection preparation for protocol tests which supply an in-memory job."""
from __future__ import annotations

from timiniprint import reporting
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.send import send_prepared_job


async def prepare_and_send(device, connection, job, *, timeout=1.0, reporter=reporting.DUMMY_REPORTER):
    prepared = await prepare_connection_runtime(device, connection, timeout=timeout, reporter=reporter)
    await send_prepared_job(prepared, connection, job, timeout=timeout, reporter=reporter)
