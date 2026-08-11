from __future__ import annotations

from typing import TYPE_CHECKING

from ...protocol.family import ProtocolFamily
from .base import RuntimeController
from .funny_lx import FunnyLxRuntimeController
from .luck_normal import LuckNormalRuntimeController
from .niimbot import NiimbotRuntimeController
from .phomemo_esc import PhomemoEscRuntimeController
from .tiny import TinyRuntimeController
from .v5c import V5CRuntimeController
from .v5g import V5GRuntimeController
from .v5x import V5XRuntimeController
from .yk_astra_p1 import AstraP1RuntimeController

if TYPE_CHECKING:
    from ...devices import PrinterDevice


def runtime_controller_for_device(device: PrinterDevice) -> RuntimeController | None:
    if device.protocol_family in {ProtocolFamily.TINY, ProtocolFamily.TINY_PREFIXED}:
        return TinyRuntimeController()
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
        device.protocol_family is ProtocolFamily.PHOMEMO_ESC
        and device.protocol_variant in {"printmaster_m110", "printmaster_m120"}
    ):
        return PhomemoEscRuntimeController()
    if device.protocol_family is ProtocolFamily.YK_ASTRA_P1:
        return AstraP1RuntimeController(
            paper_query_on_valid=device.protocol_variant == "s001",
        )
    if (
        device.protocol_family is ProtocolFamily.LUCK_NORMAL
        and device.protocol_variant in {"lujiang_normal", "lujiang_normal_h"}
    ):
        return LuckNormalRuntimeController(protocol_variant=device.protocol_variant)
    return None
