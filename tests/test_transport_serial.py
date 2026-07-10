from __future__ import annotations

import asyncio
import builtins
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from timiniprint.devices import PrinterCatalog, SerialTarget
from timiniprint.protocol import ProtocolJob, ProtocolStep
from timiniprint.transport.serial import SerialConnection, SerialConnector


class _FakeSerialHandle:
    def __init__(self):
        self.writes = []
        self.flushed = False

    def write(self, data: bytes):
        self.writes.append(bytes(data))

    def flush(self):
        self.flushed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class TransportSerialTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = PrinterCatalog.load()
        self.device = catalog.device_from_profile(
            "x6h",
            display_name="x6h",
            transport_target=SerialTarget("/dev/ttyS0"),
        )

    def test_write_blocking_chunks_and_flush(self) -> None:
        handle = _FakeSerialHandle()

        class _SerialFactory:
            def __call__(self, *_args, **_kwargs):
                return handle

        fake_mod = types.SimpleNamespace(Serial=_SerialFactory())
        sys.modules["serial"] = fake_mod
        try:
            t = SerialConnection(self.device)
            with patch("time.sleep") as sleep_mock:
                t._write_blocking(b"abcdef", chunk_size=2, delay_ms=5)
            self.assertEqual(handle.writes, [b"ab", b"cd", b"ef"])
            self.assertTrue(handle.flushed)
            self.assertGreaterEqual(sleep_mock.call_count, 1)
        finally:
            sys.modules.pop("serial", None)

    def test_write_blocking_missing_pyserial(self) -> None:
        original_import = builtins.__import__

        def _import(name, *args, **kwargs):
            if name == "serial":
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        t = SerialConnection(self.device)
        with patch("builtins.__import__", side_effect=_import):
            with self.assertRaisesRegex(RuntimeError, "pyserial is required"):
                t._write_blocking(b"x", chunk_size=1, delay_ms=0)

    def test_write_blocking_wraps_write_error(self) -> None:
        class _Broken:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                raise OSError("boom")

            def __exit__(self, *_):
                return False

        sys.modules["serial"] = types.SimpleNamespace(Serial=_Broken)
        try:
            t = SerialConnection(self.device)
            with self.assertRaisesRegex(RuntimeError, "Serial connection failed"):
                t._write_blocking(b"abc", 2, 0)
        finally:
            sys.modules.pop("serial", None)

    def test_send_async_uses_executor(self) -> None:
        connection = SerialConnection(self.device)
        job = ProtocolJob(payload=b"abc")

        async def run():
            loop = asyncio.get_running_loop()
            with patch.object(loop, "run_in_executor", new=AsyncMock(return_value=None)) as run_exec:
                await connection.send(job)
                run_exec.assert_awaited_once()

        asyncio.run(run())

    def test_send_rejects_job_with_execution_steps(self) -> None:
        connection = SerialConnection(self.device)
        job = ProtocolJob(steps=(ProtocolStep.send("print data", b"abc"),))

        async def run() -> None:
            with self.assertRaisesRegex(RuntimeError, "ConnectedPrinter.send_job"):
                await connection.send(job)

        asyncio.run(run())

    def test_connector_returns_connection(self) -> None:
        async def run() -> None:
            connection = await SerialConnector().connect(self.device)
            self.assertIsInstance(connection, SerialConnection)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
