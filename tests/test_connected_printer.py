from __future__ import annotations

import os
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from tests.helpers import install_crc8_stub

install_crc8_stub()

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.connected import connect_printer
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import ProtocolJob


class _Connection:
    def __init__(self) -> None:
        self.sent_jobs: list[ProtocolJob] = []
        self.disconnects = 0

    async def send(self, job: ProtocolJob) -> None:
        self.sent_jobs.append(job)

    async def disconnect(self) -> None:
        self.disconnects += 1


class _Connector:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.connected_devices = []

    async def connect(self, device):
        self.connected_devices.append(device)
        return self.connection


class ConnectedPrinterTests(unittest.IsolatedAsyncioTestCase):
    async def test_connected_printer_sends_jobs_and_paper_motion(self) -> None:
        device = PrinterCatalog.load().device_from_profile("x6h")
        connection = _Connection()
        connector = _Connector(connection)

        connected = await connect_printer(device, connector)
        self.assertFalse(hasattr(connected, "device"))
        self.assertFalse(hasattr(connected, "connection"))
        self.assertFalse(hasattr(connected, "runtime_context"))
        self.assertFalse(hasattr(connected, "reporter"))
        job = ProtocolJob(payload=b"abc")
        await connected.send_job(job)
        await connected.feed()
        await connected.disconnect()

        self.assertEqual(connector.connected_devices, [device])
        self.assertEqual(connection.sent_jobs[0], job)
        self.assertGreater(len(connection.sent_jobs[1].payload), 0)
        self.assertEqual(connection.disconnects, 1)

    async def test_connected_printer_print_file_builds_and_sends_job(self) -> None:
        device = PrinterCatalog.load().device_from_profile("x6h")
        connection = _Connection()
        connector = _Connector(connection)
        job = ProtocolJob(payload=b"built")
        settings = PrintSettings()
        builder = MagicMock()
        builder.build_from_file.return_value = job

        connected = await connect_printer(device, connector)
        with patch("timiniprint.printing.connected.PrintJobBuilder", return_value=builder) as builder_cls:
            await connected.print_file("input.png", settings=settings)

        builder_cls.assert_called_once_with(
            device,
            settings=settings,
            runtime_context=ANY,
            reporter=ANY,
        )
        builder.build_from_file.assert_called_once_with("input.png")
        self.assertEqual(connection.sent_jobs, [job])

    async def test_connected_printer_print_text_builds_temp_file_and_cleans_it_up(self) -> None:
        device = PrinterCatalog.load().device_from_profile("x6h")
        connection = _Connection()
        connector = _Connector(connection)
        job = ProtocolJob(payload=b"text")
        captured = {}
        builder = MagicMock()

        def build_from_file(path: str) -> ProtocolJob:
            captured["path"] = path
            with open(path, encoding="utf-8") as handle:
                captured["text"] = handle.read()
            return job

        builder.build_from_file.side_effect = build_from_file

        connected = await connect_printer(device, connector)
        with patch("timiniprint.printing.connected.PrintJobBuilder", return_value=builder):
            await connected.print_text("hello")

        self.assertEqual(captured["text"], "hello")
        self.assertFalse(os.path.exists(captured["path"]))
        self.assertEqual(connection.sent_jobs, [job])

    async def test_connected_printer_context_manager_disconnects(self) -> None:
        device = PrinterCatalog.load().device_from_profile("x6h")
        connection = _Connection()
        connector = _Connector(connection)

        async with await connect_printer(device, connector) as connected:
            await connected.send_job(ProtocolJob(payload=b"context"))
            self.assertEqual(connection.disconnects, 0)

        self.assertEqual(connection.disconnects, 1)
        self.assertEqual(connection.sent_jobs[0].payload, b"context")

    async def test_connect_printer_disconnects_when_runtime_prepare_fails(self) -> None:
        device = PrinterCatalog.load().device_from_profile("x6h")
        connection = _Connection()
        connector = _Connector(connection)

        with patch(
            "timiniprint.printing.connected.prepare_connection_runtime",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await connect_printer(device, connector)

        self.assertEqual(connection.disconnects, 1)


if __name__ == "__main__":
    unittest.main()
