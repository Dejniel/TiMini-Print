from __future__ import annotations

from typing import TYPE_CHECKING

from ... import reporting
from .base import PreparedPrinter, RuntimeController
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
    controller: RuntimeController | None = None,
) -> PreparedPrinter:
    """Resolve one connection before publishing its immutable print configuration.

    An explicit controller is a bootstrap strategy. It may select another
    family and return its prepared runtime, preserving negotiated state.
    The sending path never creates controllers or repeats this preparation.
    """

    controller = controller if controller is not None else runtime_controller_for_device(device)
    if controller is None:
        return PreparedPrinter(device)
    session = RuntimeConnectionSession(connection, reporter=reporter)
    await session.attach_runtime_controller(controller, timeout=timeout)
    prepared = await controller.prepare(device, session, timeout=timeout)
    _validate_preparation(device, prepared, controller, connection)
    if prepared.runtime_controller is not controller:
        if not session.can_observe_replies():
            # Without an adapter-owned controller, preparation owns its lifecycle.
            await controller.stop(session)
        await session.attach_runtime_controller(prepared.runtime_controller, timeout=timeout)
    return prepared


def _validate_preparation(
    connected_device: PrinterDevice,
    prepared: PreparedPrinter,
    bootstrap: RuntimeController,
    connection: PrinterConnection,
) -> None:
    resolved_device = prepared.device
    changed: list[str] = []
    if (
        resolved_device.protocol_family != connected_device.protocol_family
        and prepared.runtime_controller is bootstrap
    ):
        raise RuntimeError("Selecting another protocol family requires its prepared runtime")
    if hasattr(connection, "active_ble_profile"):
        active_ble_profile = connection.active_ble_profile
        if active_ble_profile is not None and resolved_device.ble_transport_profile != active_ble_profile:
            changed.append("ble_transport_profile")
    elif resolved_device.ble_transport_profile != connected_device.ble_transport_profile:
        raise RuntimeError(
            "Selecting a different BLE profile requires connection.active_ble_profile "
            "to identify the active transport"
        )
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
            "Connection preparation cannot change active transport fields: "
            + ", ".join(changed)
        )
