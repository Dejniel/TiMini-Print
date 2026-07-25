from __future__ import annotations

from typing import TYPE_CHECKING

from ... import reporting
from .base import PreparedRuntimeContext
from .factory import runtime_controller_for_device
from .session import RuntimeConnectionSession

if TYPE_CHECKING:
    from ...devices import PrinterDevice
    from ...transport.base import PrinterConnection


async def prepare_connection_runtime(
    device: PrinterDevice,
    connection: PrinterConnection,
    *,
    timeout: float = 1.0,
    reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
) -> PreparedRuntimeContext:
    """Prepare one live connection for runtime-capability-sensitive printing."""

    controller = runtime_controller_for_device(device)
    if controller is None:
        return PreparedRuntimeContext()
    session = RuntimeConnectionSession(connection, reporter=reporter)
    await session.attach_runtime_controller(controller, timeout=timeout)
    await controller.probe_capabilities(session, timeout=timeout)
    resolved_device = controller.resolve_device(device)
    _validate_runtime_resolved_device(device, resolved_device)
    return PreparedRuntimeContext(
        runtime_controller=controller,
        capabilities=controller.runtime_capabilities(),
        resolved_device=resolved_device,
    )


def _validate_runtime_resolved_device(
    connected_device: PrinterDevice,
    resolved_device: PrinterDevice,
) -> None:
    changed: list[str] = []
    if resolved_device.protocol_family != connected_device.protocol_family:
        changed.append("protocol_family")
    if resolved_device.transport_target != connected_device.transport_target:
        changed.append("transport_target")
    if resolved_device.profile.use_spp != connected_device.profile.use_spp:
        changed.append("profile.use_spp")
    if resolved_device.profile.stream != connected_device.profile.stream:
        changed.append("profile.stream")
    if (
        resolved_device.profile.ble_mtu_request
        != connected_device.profile.ble_mtu_request
    ):
        changed.append("profile.ble_mtu_request")
    if changed:
        raise RuntimeError(
            "Runtime device resolution cannot change active transport fields: "
            + ", ".join(changed)
        )
