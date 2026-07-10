from __future__ import annotations

from typing import TYPE_CHECKING

from ...protocol.family import ProtocolFamily
from .base import RuntimeController
from .funny_lx import FunnyLxRuntimeController
from .luck_normal import LuckNormalRuntimeController
from .niimbot import NiimbotRuntimeController
from .v5c import V5CRuntimeController
from .v5g import V5GRuntimeController
from .v5x import V5XRuntimeController

if TYPE_CHECKING:
    from ...devices import PrinterDevice


def runtime_controller_for_device(device: PrinterDevice) -> RuntimeController | None:
    if device.protocol_family is ProtocolFamily.V5G:
        return V5GRuntimeController(
            runtime_settings=device.runtime_settings,
        )
    if device.protocol_family is ProtocolFamily.V5X:
        return V5XRuntimeController()
    if device.protocol_family is ProtocolFamily.V5C:
        return V5CRuntimeController()
    if device.protocol_family is ProtocolFamily.NIIMBOT:
        return NiimbotRuntimeController()
    if device.protocol_family is ProtocolFamily.FUNNY_LX:
        return FunnyLxRuntimeController(bluetooth_address=device.address)
    if (
        device.protocol_family is ProtocolFamily.LUCK_NORMAL
        and device.protocol_variant in {"lujiang_normal", "lujiang_normal_h"}
    ):
        return LuckNormalRuntimeController(protocol_variant=device.protocol_variant)
    return None
