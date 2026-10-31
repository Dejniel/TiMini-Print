"""Failed cleanup is typed separately from ordinary chained operation errors."""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from timiniprint import reporting
from timiniprint.devices import PrinterCatalog, SerialTarget
from timiniprint.printing.connected import connect_printer
from timiniprint.transport import ConnectionCleanupError
from timiniprint.transport.bluetooth.adapters.adapter_fallback import _FallbackSocket
from timiniprint.transport.bluetooth.backend import SppBackend
from timiniprint.transport.bluetooth.types import DeviceInfo
from timiniprint.transport.serial import SerialConnector


@pytest.mark.parametrize("wrapped", [False, True])
def test_cleanup_marker_is_recognized_through_explicit_cause_wrappers(wrapped):
    original = asyncio.CancelledError()
    original.__cause__ = ConnectionCleanupError(OSError("close failed"))
    error = TimeoutError()
    error.__cause__ = original
    assert ConnectionCleanupError.is_cause_of(error if wrapped else original)
    assert ConnectionCleanupError.is_cause_of(original.__cause__)


def test_ordinary_and_circular_exception_causes_are_not_cleanup_markers():
    error = RuntimeError("ordinary chained error")
    error.__cause__ = OSError("ordinary cause")
    assert not ConnectionCleanupError.is_cause_of(error)
    error.__cause__.__cause__ = error
    assert not ConnectionCleanupError.is_cause_of(error)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("close_error", [None, OSError, asyncio.CancelledError])
def test_preparation_preserves_the_original_error_and_marks_only_failed_cleanup(cancel, close_error):
    async def run():
        original = asyncio.CancelledError() if cancel else ValueError("setup failed")
        connection = AsyncMock()
        if close_error:
            connection.disconnect.side_effect = close_error("close failed")
        connector = AsyncMock()
        connector.connect.return_value = connection
        device = PrinterCatalog.load().device_from_profile("x6h")
        with patch("timiniprint.printing.connected.prepare_connection_runtime", side_effect=original):
            with pytest.raises(type(original)) as caught:
                await connect_printer(device, connector)
        assert caught.value is original
        assert isinstance(original.__cause__, ConnectionCleanupError) is (close_error is not None)
        if close_error is OSError:
            assert str(original.__cause__.__cause__) == "close failed"
        connection.disconnect.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("close_error", [None, OSError, asyncio.CancelledError])
def test_serial_open_failure_marks_failed_cleanup_without_masking_the_open_error(close_error):
    async def run():
        original = ValueError("open failed")
        device = PrinterCatalog.load().device_from_profile("x6h").with_transport_target(SerialTarget("/dev/test", 19200))
        with patch("timiniprint.transport.serial.SerialConnection.open", side_effect=original), \
             patch("timiniprint.transport.serial.SerialConnection.disconnect",
                   side_effect=close_error("close failed") if close_error else None) as close:
            with pytest.raises(ValueError) as caught:
                await SerialConnector().connect(device)
        assert caught.value is original
        assert isinstance(original.__cause__, ConnectionCleanupError) is (close_error is not None)
        close.assert_awaited_once()
    asyncio.run(run())


def test_backend_async_cleanup_failure_cannot_be_swallowed():
    async def run():
        original = ValueError("open failed")
        backend = SppBackend()
        with patch.object(backend, "_connect_attempts_blocking", side_effect=original), \
             patch.object(backend, "disconnect", side_effect=OSError("close failed")):
            with pytest.raises(ValueError) as caught:
                await backend.connect(DeviceInfo("test", "AA"))
        assert caught.value is original
        assert isinstance(original.__cause__, ConnectionCleanupError)
        assert str(original.__cause__.__cause__) == "close failed"
    asyncio.run(run())


def test_ble_fallback_does_not_open_another_socket_after_failed_cleanup():
    primary, fallback = Mock(), Mock()
    original = ValueError("open failed")
    primary.connect.side_effect = original
    primary.close.side_effect = OSError("close failed")
    socket = _FallbackSocket(primary=primary, fallback=fallback,
                             primary_name="first", fallback_name="second", reporter=reporting.DUMMY_REPORTER)
    with pytest.raises(ValueError) as caught:
        socket.connect(("AA", 1))
    assert caught.value is original
    assert isinstance(original.__cause__, ConnectionCleanupError)
    fallback.connect.assert_not_called()
