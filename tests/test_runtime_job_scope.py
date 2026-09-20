from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.base import PreparedPrinter, RuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ProtocolJob, ProtocolStep


@pytest.mark.parametrize("path", ["payload", "steps", "handled", "fallback"])
@pytest.mark.parametrize("failure", [None, "send", "completion", "cancel"])
def test_job_scope_covers_all_paths_and_releases_on_error(path, failure):
    events = []

    class Controller(RuntimeController):
        @asynccontextmanager
        async def job_scope(self, session, job, *, timeout):
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")

        async def send_protocol_steps(self, session, steps, *, timeout):
            if path != "handled":
                return False
            await send()
            return True

        async def wait_for_completion(self, session, *, timeout):
            events.append("completion")
            if failure == "completion":
                raise RuntimeError("completion failed")

    async def send(*args):
        assert events == ["enter"]
        events.append("send")
        if failure == "send":
            raise OSError("send failed")
        if failure == "cancel":
            raise asyncio.CancelledError()

    class Connection:
        async def send(self, job):
            await send()

    async def run():
        connection = Connection()
        if path == "steps":
            connection.send_standard_payload = send
        job = ProtocolJob(payload=b"raster", wait_for_completion=True,
                          steps=(ProtocolStep.send("raster", b"raster"),) if path != "payload" else ())
        prepared = PreparedPrinter(PrinterCatalog.load().device_from_model("printmaster_m110"), Controller())
        if failure:
            error = {"send": OSError, "completion": RuntimeError, "cancel": asyncio.CancelledError}[failure]
            with pytest.raises(error):
                await send_prepared_job(prepared, connection, job)
        else:
            await send_prepared_job(prepared, connection, job)
        assert events == ["enter", "send"] + ([] if failure in ("send", "cancel") else ["completion"]) + ["exit"]

    asyncio.run(run())
