import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from timiniprint.devices.bluetooth_profiles import BleTransportProfile
from timiniprint.transport.bluetooth import device_information
from timiniprint.transport.bluetooth import bleak_lifecycle
from timiniprint.transport.bluetooth.adapters import bleak_adapter
from timiniprint.transport.bluetooth.backend import SppBackend
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport
from timiniprint.transport.bluetooth.adapters.adapter_fallback import _FallbackSocket


def client():
    write = SimpleNamespace(uuid="0000ff02-0000-1000-8000-00805f9b34fb", properties=["write"])
    notify = SimpleNamespace(uuid="0000ff01-0000-1000-8000-00805f9b34fb", properties=["notify"])
    return SimpleNamespace(
        services=[SimpleNamespace(uuid="0000ff00-0000-1000-8000-00805f9b34fb",
                                  characteristics=[write, notify])],
        connect=AsyncMock(), disconnect=AsyncMock(),
        start_notify=AsyncMock(), stop_notify=AsyncMock(), mtu_size=None,
    )


async def slow_operation(*args, **kwargs):
    # A broken timeout must fail this test, not hang the suite indefinitely.
    await asyncio.sleep(0.5)


@pytest.mark.parametrize("stage", ["resolution", "connect", "pair", "information", "notify", "initialize"])
def test_socket_deadline_covers_every_setup_stage(monkeypatch, stage):
    transport = client()
    sock = bleak_adapter._BleakSocket(
        device_cache={"A": object()}, ble_profile=BleTransportProfile(prefer_generic_notify=True),
    )
    sock.settimeout(0.02)
    monkeypatch.setattr(bleak_adapter, "IS_MACOS", False)
    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakClient=lambda *a, **kw: transport))
    if stage == "resolution":
        monkeypatch.setattr(sock, "_resolve_client_target", slow_operation)
    elif stage == "connect":
        transport.connect.side_effect = slow_operation
    elif stage == "pair":
        sock._pairing_hint = True
        transport.pair = AsyncMock(side_effect=slow_operation)
    elif stage == "information":
        monkeypatch.setattr(bleak_adapter, "read_device_information", slow_operation)
    elif stage == "notify":
        transport.start_notify.side_effect = slow_operation
    else:
        monkeypatch.setattr(sock._transport, "initialize_connection", slow_operation)
    with pytest.raises(TimeoutError, match="connection setup"):
        sock.connect(("A", 1))
    assert not sock._connected and sock._client is None and sock._loop is None
    assert transport.disconnect.await_count == (stage != "resolution")


def test_disconnect_finishes_cancellation_delayed_inside_connect():
    async def run():
        lifecycle = bleak_lifecycle._BleakLifecycle()
        release = asyncio.Event()
        cancelled = asyncio.Event()

        async def connecting():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()

        with pytest.raises(TimeoutError):
            await lifecycle.run(connecting(), timeout=0.01, label="connection setup")
        await cancelled.wait()
        transport = SimpleNamespace(disconnect=AsyncMock(side_effect=release.set))
        await lifecycle.disconnect(transport, timeout=0.1)
        transport.disconnect.assert_awaited_once()
        assert not lifecycle._tasks

    asyncio.run(run())


@pytest.mark.parametrize("stop_fails", [False, True])
def test_notification_shutdown_does_not_prevent_disconnect(stop_fails):
    async def run():
        reporter = MagicMock()
        lifecycle = bleak_lifecycle._BleakLifecycle(reporter)
        transport = client()
        stop = AsyncMock(side_effect=OSError("notify failed") if stop_fails else slow_operation)
        await lifecycle.disconnect(transport, before_disconnect=stop(), timeout=0.04)
        transport.disconnect.assert_awaited_once()
        reporter.warning.assert_called_once()
        assert not lifecycle._tasks

    asyncio.run(run())


def test_socket_close_failure_keeps_resources_for_retry_and_marks_unusable(monkeypatch):
    monkeypatch.setattr(bleak_lifecycle, "BLE_CLEANUP_TIMEOUT", 0.04)
    for failure in (OSError("disconnect failed"), slow_operation):
        transport = client()
        transport.disconnect.side_effect = failure
        sock = bleak_adapter._BleakSocket()
        sock._loop = asyncio.new_event_loop()
        sock._client = transport
        sock._connected = True
        try:
            with pytest.raises((OSError, TimeoutError, RuntimeError)):
                sock.close()
            assert not sock._connected
            assert sock._client is transport and not sock._loop.is_closed()
            with pytest.raises(RuntimeError, match="Not connected"):
                sock.send(b"x")
        finally:
            transport.disconnect.side_effect = None
            sock.close()
        assert sock._client is None and sock._loop is None
        sock.close()  # Idempotent after successful cleanup.


def test_socket_preserves_connect_failure_when_cleanup_also_fails(monkeypatch):
    transport = client()
    transport.connect.side_effect = OSError("connect failed")
    transport.disconnect.side_effect = OSError("disconnect failed")
    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakClient=lambda *a, **kw: transport))
    monkeypatch.setattr(bleak_adapter, "IS_MACOS", False)
    sock = bleak_adapter._BleakSocket(device_cache={"A": object()})
    try:
        with pytest.raises(RuntimeError, match="connect failed") as error:
            sock.connect(("A", 1))
        assert str(error.value.__cause__) == "disconnect failed"
        assert sock._client is transport and not sock._connected
    finally:
        transport.disconnect.side_effect = None
        sock.close()


def test_failed_notify_stop_is_logged_but_successful_disconnect_closes_socket(monkeypatch):
    transport, reporter = client(), MagicMock()
    transport.stop_notify.side_effect = OSError("notify failed")
    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakClient=lambda *a, **kw: transport))
    monkeypatch.setattr(bleak_adapter, "IS_MACOS", False)
    sock = bleak_adapter._BleakSocket(device_cache={"A": object()}, reporter=reporter,
                                    ble_profile=BleTransportProfile(prefer_generic_notify=True))
    sock.connect(("A", 1))
    sock.close()
    transport.disconnect.assert_awaited_once()
    assert sock._client is None and sock._loop is None
    assert any("notify failed" in call.kwargs.get("detail", "")
               for call in reporter.warning.call_args_list)


def test_cleanup_reports_operations_that_ignore_cancellation():
    async def run():
        lifecycle = bleak_lifecycle._BleakLifecycle()
        release = asyncio.Event()

        async def stubborn():
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass

        try:
            with pytest.raises(TimeoutError):
                await lifecycle.run(stubborn(), timeout=0.01, label="connection setup")
            with pytest.raises(RuntimeError, match="operations are still pending"):
                await lifecycle.disconnect(client(), timeout=0.02)
            assert lifecycle._tasks
        finally:
            release.set()
            await asyncio.gather(*lifecycle._tasks)
        await lifecycle.disconnect(client(), timeout=0.02)
        assert not lifecycle._tasks

    asyncio.run(run())


def test_disconnect_checks_the_reported_link_state():
    async def run():
        transport = client()
        transport.is_connected = True
        lifecycle = bleak_lifecycle._BleakLifecycle()
        with pytest.raises(RuntimeError, match="client is still connected"):
            await lifecycle.disconnect(transport)
        transport.is_connected = False
        await lifecycle.disconnect(transport)

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["connect", "information", "disconnect"])
def test_information_probe_has_setup_and_cleanup_deadlines(monkeypatch, stage):
    transport = client()
    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakClient=lambda *a, **kw: transport))
    monkeypatch.setattr(device_information, "_PROBE_TIMEOUT", 0.02)
    monkeypatch.setattr(bleak_lifecycle, "BLE_CLEANUP_TIMEOUT", 0.04)
    if stage == "information":
        monkeypatch.setattr(device_information, "read_device_information", slow_operation)
    else:
        getattr(transport, stage).side_effect = slow_operation
    with pytest.raises((TimeoutError, RuntimeError)):
        asyncio.run(device_information.probe_ble_device_information("A"))
    transport.disconnect.assert_awaited_once()


def test_probe_preserves_connect_error_when_disconnect_also_fails(monkeypatch):
    transport = client()
    transport.connect.side_effect = OSError("connect failed")
    transport.disconnect.side_effect = OSError("disconnect failed")
    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakClient=lambda *a, **kw: transport))
    with pytest.raises(OSError, match="connect failed") as error:
        asyncio.run(device_information.probe_ble_device_information("A"))
    assert str(error.value.__cause__) == "disconnect failed"


def test_repeated_probe_cancellation_waits_for_disconnect(monkeypatch):
    async def run():
        connecting, disconnecting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        transport = client()

        async def connect():
            connecting.set()
            await asyncio.Event().wait()

        async def disconnect():
            disconnecting.set()
            await release.wait()

        transport.connect.side_effect = connect
        transport.disconnect.side_effect = disconnect
        monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakClient=lambda *a, **kw: transport))
        task = asyncio.create_task(device_information.probe_ble_device_information("A"))
        await connecting.wait()
        task.cancel()
        await disconnecting.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        transport.disconnect.assert_awaited_once()

    asyncio.run(run())


def test_backend_keeps_failed_close_and_does_not_open_fallback(monkeypatch):
    import timiniprint.transport.bluetooth.backend as backend_module

    sock = SimpleNamespace(connect=MagicMock(side_effect=OSError("connect failed")),
                           close=MagicMock(side_effect=OSError("close failed")))
    adapter = SimpleNamespace(ensure_paired=MagicMock(), resolve_rfcomm_channels=lambda address: [1],
                              create_socket=MagicMock(return_value=sock))
    monkeypatch.setattr(backend_module, "_select_adapter", lambda transport: adapter)
    backend = SppBackend()
    attempts = [DeviceInfo("A", "A", transport=transport)
                for transport in (DeviceTransport.BLE, DeviceTransport.CLASSIC)]
    with pytest.raises(OSError, match="connect failed") as error:
        backend._connect_attempts_blocking(attempts, None)
    assert str(error.value.__cause__) == "close failed"
    assert not backend.is_connected() and backend._sock is sock
    adapter.create_socket.assert_called_once()
    sock.close.side_effect = None
    backend._disconnect_blocking()
    assert backend._sock is None


def test_backend_disconnect_failure_is_unusable_and_can_be_retried():
    sock = SimpleNamespace(close=MagicMock(side_effect=OSError("close failed")))
    backend = SppBackend()
    backend._sock, backend._connected, backend._transport = sock, True, DeviceTransport.BLE
    with pytest.raises(OSError, match="close failed"):
        asyncio.run(backend.disconnect())
    assert not backend.is_connected() and backend.transport is None
    assert backend._sock is sock
    sock.close.side_effect = None
    asyncio.run(backend.disconnect())
    assert backend._sock is None


def test_backend_closes_socket_if_timeout_configuration_fails(monkeypatch):
    import timiniprint.transport.bluetooth.backend as backend_module

    sock = SimpleNamespace(settimeout=MagicMock(side_effect=ValueError("invalid timeout")),
                           connect=MagicMock(), close=MagicMock())
    adapter = SimpleNamespace(ensure_paired=MagicMock(), resolve_rfcomm_channels=lambda address: [1],
                              create_socket=MagicMock(return_value=sock))
    monkeypatch.setattr(backend_module, "_select_adapter", lambda transport: adapter)
    backend = SppBackend()
    with pytest.raises(RuntimeError, match="invalid timeout"):
        backend._connect_attempts_blocking([DeviceInfo("A", "A", transport=DeviceTransport.BLE)], None)
    sock.close.assert_called_once()
    sock.connect.assert_not_called()
    assert backend._sock is None


@pytest.mark.parametrize("failed_backend", ["primary", "fallback"])
def test_backend_wrapper_does_not_swallow_failed_connect_cleanup(failed_backend):
    primary = SimpleNamespace(connect=MagicMock(side_effect=OSError("primary connect")), close=MagicMock())
    fallback = SimpleNamespace(connect=MagicMock(side_effect=OSError("fallback connect")), close=MagicMock())
    failed = primary if failed_backend == "primary" else fallback
    failed.close.side_effect = OSError("close failed")
    sock = _FallbackSocket(primary=primary, fallback=fallback, primary_name="primary",
                           fallback_name="fallback", reporter=MagicMock())
    with pytest.raises(OSError, match=f"{failed_backend} connect") as error:
        sock.connect(("A", 1))
    assert str(error.value.__cause__) == "close failed"
    assert fallback.connect.call_count == (failed_backend == "fallback")
    failed.close.side_effect = None
    sock.close()


def test_backend_wrapper_attempts_both_closes_and_allows_retry():
    primary = SimpleNamespace(connect=MagicMock(), close=MagicMock(side_effect=OSError("close failed")))
    fallback = SimpleNamespace(close=MagicMock())
    sock = _FallbackSocket(primary=primary, fallback=fallback, primary_name="primary",
                           fallback_name="fallback", reporter=MagicMock())
    sock.connect(("A", 1))
    with pytest.raises(OSError, match="close failed"):
        sock.close()
    fallback.close.assert_called_once()
    primary.close.side_effect = None
    sock.close()
    assert primary.close.call_count == fallback.close.call_count == 2


@pytest.mark.parametrize("macos", [True, False])
def test_scan_caches_native_devices_only_where_used(monkeypatch, macos):
    device = SimpleNamespace(name="P1", address="A")
    adv = SimpleNamespace(local_name="P1", manufacturer_data={1: b"data"})
    scanner = SimpleNamespace(discover=AsyncMock(return_value={"A": (device, adv)}))
    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakScanner=scanner))
    monkeypatch.setattr(bleak_adapter, "IS_MACOS", macos)
    adapter = bleak_adapter._BleakBleAdapter()
    result = adapter.scan_blocking(0.01)
    assert result == [DeviceInfo("P1", "A", paired=None, transport=DeviceTransport.BLE,
                                 manufacturer_data=(b"\x01\x00data",))]
    assert adapter._device_cache == ({} if macos else {"A": device})


def test_cancelled_discovery_finishes_scanner_cleanup(monkeypatch):
    events = []

    async def discover(**kwargs):
        try:
            raise asyncio.CancelledError()
        finally:
            await asyncio.sleep(0)
            events.append("scanner stopped")

    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(BleakScanner=SimpleNamespace(discover=discover)))
    adapter = bleak_adapter._BleakBleAdapter()
    with pytest.raises(asyncio.CancelledError):
        adapter.scan_blocking(0.01)
    assert events == ["scanner stopped"] and adapter._device_cache == {}
