from __future__ import annotations

from collections.abc import Callable

from ... import reporting
from ...devices import BluetoothTarget, PrinterDevice, bluetooth_connection_plan
from ...devices.device import BluetoothEndpointTransport
from ...protocol import ProtocolJob
from .backend import SppBackend
from .types import DeviceInfo, DeviceTransport


class BleakBluetoothConnection:
    """Bluetooth connection backed by the repo's Bleak/Spp transport stack."""

    def __init__(
        self,
        backend: SppBackend,
        device: PrinterDevice,
        reporter: reporting.Reporter,
    ) -> None:
        target = device.transport_target
        if not isinstance(target, BluetoothTarget):
            raise RuntimeError("BleakBluetoothConnector requires a PrinterDevice with BluetoothTarget")
        self._backend = backend
        self._device = device
        self._target = target
        self._reporter = reporter

    @property
    def reporter(self) -> reporting.Reporter:
        return self._reporter

    async def attach_runtime_controller(self, runtime_controller, *, timeout: float = 1.0) -> None:
        await self._backend.attach_runtime_controller(runtime_controller, timeout=timeout)

    def can_send_control_packet(self) -> bool:
        return self._backend.can_send_control_packet()

    def can_send_bulk_payload(self) -> bool:
        return self._backend.can_send_bulk_payload()

    def can_query_control_packet(self) -> bool:
        return self._backend.can_query_control_packet()

    def can_wait_for_notification(self) -> bool:
        return self._backend.can_wait_for_notification()

    def can_send_control_packet_wait_notification(self) -> bool:
        return self._backend.can_send_control_packet_wait_notification()

    async def send_control_packet(self, packet: bytes, *, timeout: float = 1.0) -> bool:
        return await self._backend.send_control_packet(packet, timeout=timeout)

    async def send_bulk_payload(self, data: bytes, *, timeout: float = 1.0) -> bool:
        return await self._backend.send_bulk_payload(data, timeout=timeout)

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete: Callable[[bytes], bool] | None = None,
    ) -> bytes | None:
        return await self._backend.query_control_packet(
            packet,
            timeout=timeout,
            reply_complete=reply_complete,
        )

    async def wait_for_notification(
        self,
        label: str,
        match: Callable[[bytes], bool],
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        return await self._backend.wait_for_notification(
            label,
            match,
            timeout=timeout,
            required=required,
        )

    async def send_control_packet_wait_notification(
        self,
        packet: bytes,
        *,
        label: str,
        match: Callable[[bytes], bool],
        timeout: float,
        required: bool = True,
    ) -> bytes | None:
        return await self._backend.send_control_packet_wait_notification(
            packet,
            label=label,
            match=match,
            timeout=timeout,
            required=required,
        )

    async def send(self, job: ProtocolJob) -> None:
        """Send a protocol job using the device's stream settings and runtime state."""
        await self._send_payload(job.payload)

    async def send_standard_payload(self, data: bytes) -> None:
        """Send raw protocol payload using the device's stream settings."""
        await self._send_payload(data)

    async def _send_payload(self, data: bytes) -> None:
        await self._backend.write(
            data,
            self._device.profile.stream.chunk_size,
            self._device.profile.stream.delay_ms,
        )

    async def disconnect(self) -> None:
        """Close the underlying Bluetooth backend connection."""
        await self._backend.disconnect()


class BleakBluetoothConnector:
    """Create Bluetooth connections for devices with ``BluetoothTarget``."""

    def __init__(self, reporter: reporting.Reporter = reporting.DUMMY_REPORTER) -> None:
        self._reporter = reporter

    async def connect(self, device: PrinterDevice) -> BleakBluetoothConnection:
        """Connect to a resolved Bluetooth device and return a live connection."""
        target = device.transport_target
        if not isinstance(target, BluetoothTarget):
            raise RuntimeError("BleakBluetoothConnector requires a PrinterDevice with BluetoothTarget")
        plan = bluetooth_connection_plan(device)
        attempts = [
            self._to_device_info(attempt.endpoint, device)
            for attempt in plan.attempts
        ]
        backend = SppBackend(reporter=self._reporter)
        await backend.connect_attempts(
            attempts,
            pairing_hint=plan.pairing_hint,
        )
        return BleakBluetoothConnection(backend, device, self._reporter)

    @staticmethod
    def _to_device_info(endpoint, device: PrinterDevice) -> DeviceInfo:
        transport = (
            DeviceTransport.BLE
            if endpoint.transport is BluetoothEndpointTransport.BLE
            else DeviceTransport.CLASSIC
        )
        return DeviceInfo(
            name=endpoint.name,
            address=endpoint.address,
            paired=endpoint.paired,
            transport=transport,
            protocol_family=device.protocol_family,
            ble_mtu_request=(
                device.profile.ble_mtu_request
                if transport is DeviceTransport.BLE
                else None
            ),
        )
