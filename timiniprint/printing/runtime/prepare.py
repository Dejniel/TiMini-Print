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
    return PreparedRuntimeContext(
        runtime_controller=controller,
        capabilities=controller.runtime_capabilities(),
    )
