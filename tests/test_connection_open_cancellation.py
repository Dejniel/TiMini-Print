"""Cancellation must stop fallback without abandoning in-flight native I/O."""
import asyncio
import threading
from unittest.mock import Mock, patch

import pytest

from timiniprint import reporting
from timiniprint.transport import ConnectionCleanupError
from timiniprint.transport.bluetooth.adapters.adapter_fallback import _FallbackSocket
from timiniprint.transport.bluetooth.backend import SppBackend
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport


@pytest.mark.parametrize("phase,open_fails,cleanup_fails", [
    ("pairing", False, False), ("discovery", False, False), ("creation", False, False),
    ("connect", False, False), ("connect", True, False), ("connect", True, True),
])
def test_cancellation_stops_later_native_opens_but_waits_for_current_call(phase, open_fails, cleanup_fails):
    async def run():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        socket = Mock()
        adapter = Mock()
        adapter.create_socket.return_value = socket
        adapter.resolve_rfcomm_channels.return_value = [1, 2]
        operation = {"pairing": adapter.ensure_paired, "discovery": adapter.resolve_rfcomm_channels,
                     "creation": adapter.create_socket, "connect": socket.connect}[phase]

        def blocked(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2), "native call was not released"
            if open_fails:
                error = OSError("open failed")
                if cleanup_fails:
                    error.__cause__ = ConnectionCleanupError(OSError("hidden close failed"))
                raise error
            return socket if phase == "creation" else [1, 2]

        operation.side_effect = blocked
        backend = SppBackend()
        attempts = [DeviceInfo("test", "AA", transport=transport)
                    for transport in (DeviceTransport.CLASSIC, DeviceTransport.BLE)]
        with patch("timiniprint.transport.bluetooth.backend._select_adapter", return_value=adapter) as select:
            task = asyncio.create_task(backend.connect_attempts(attempts))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), "native open must finish before cleanup"
                release.set()
                with pytest.raises(asyncio.CancelledError) as caught:
                    await task
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        select.assert_called_once()
        assert socket.connect.call_count == (1 if phase == "connect" else 0)
        assert adapter.create_socket.call_count == (1 if phase in {"creation", "connect"} else 0)
        assert not backend.is_connected()
        assert ConnectionCleanupError.is_cause_of(caught.value) is cleanup_fails
        if phase in {"creation", "connect"}:
            socket.close.assert_called_once()
    asyncio.run(run())


def test_cancellation_stops_nested_ble_backend_fallback():
    async def run():
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        primary, secondary = Mock(), Mock()

        def blocked(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2), "native call was not released"
            raise OSError("open failed")

        primary.connect.side_effect = blocked
        socket = _FallbackSocket(primary=primary, fallback=secondary, primary_name="first",
                                 fallback_name="second", reporter=reporting.DUMMY_REPORTER)
        adapter = Mock()
        adapter.create_socket.return_value = socket
        adapter.resolve_rfcomm_channels.return_value = [1]
        backend = SppBackend()
        with patch("timiniprint.transport.bluetooth.backend._select_adapter", return_value=adapter):
            task = asyncio.create_task(backend.connect(DeviceInfo("test", "AA", transport=DeviceTransport.BLE)))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        secondary.connect.assert_not_called()
        assert backend._sock is None
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["endpoint", "pairing", "discovery", "channel", "ble_primary", "ble_secondary"])
def test_cleanup_marker_stops_fallback_even_without_a_retained_socket(stage):
    original = OSError("open failed")
    original.__cause__ = ConnectionCleanupError(OSError("hidden resource failed to close"))
    backend = SppBackend()
    classic = DeviceInfo("test", "AA", transport=DeviceTransport.CLASSIC)
    if stage == "endpoint":
        with patch.object(backend, "_connect_with_device", side_effect=original) as connect:
            with pytest.raises(OSError) as caught:
                backend._connect_attempts_blocking([classic, DeviceInfo("test", "BB", transport=DeviceTransport.BLE)], None)
        connect.assert_called_once()
    elif stage in {"pairing", "discovery", "channel"}:
        adapter = Mock()
        adapter.resolve_rfcomm_channels.return_value = [1, 2]
        operation = {"pairing": adapter.ensure_paired, "discovery": adapter.resolve_rfcomm_channels,
                     "channel": adapter.create_socket}[stage]
        operation.side_effect = original
        with patch("timiniprint.transport.bluetooth.backend._select_adapter", return_value=adapter):
            with pytest.raises(OSError) as caught:
                backend._connect_with_device(classic, None)
        operation.assert_called_once()
        if stage != "channel":
            adapter.create_socket.assert_not_called()
    else:
        primary, secondary = Mock(), Mock()
        primary.connect.side_effect = original if stage == "ble_primary" else OSError("primary failed")
        if stage == "ble_secondary":
            secondary.connect.side_effect = original
        socket = _FallbackSocket(primary=primary, fallback=secondary, primary_name="first",
                                 fallback_name="second", reporter=reporting.DUMMY_REPORTER)
        with pytest.raises(OSError) as caught:
            socket.connect(("AA", 1))
        primary.close.assert_called_once()
        assert secondary.connect.call_count == (1 if stage == "ble_secondary" else 0)
    assert caught.value is original
    assert ConnectionCleanupError.is_cause_of(caught.value)
    assert backend._sock is None


def test_backend_can_open_again_after_disconnect():
    async def run():
        first, second, adapter = Mock(), Mock(), Mock()
        adapter.create_socket.side_effect = [first, second]
        adapter.resolve_rfcomm_channels.return_value = [1]
        backend = SppBackend()
        device = DeviceInfo("test", "AA")
        with patch("timiniprint.transport.bluetooth.backend._select_adapter", return_value=adapter):
            try:
                await backend.connect(device)
                await backend.disconnect()
                await backend.connect(device)
                assert backend.is_connected()
            finally:
                await backend.disconnect()
        first.close.assert_called_once()
        second.close.assert_called_once()
    asyncio.run(run())
