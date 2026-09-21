"""Phomemo page execution metadata and connection capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from ...runtime import RuntimePrintCapabilities
from ...steps import ProtocolStep
from ...types import PaperMode


@dataclass(frozen=True)
class PhomemoCapabilities(RuntimePrintCapabilities):
    """Connection snapshot; reported features do not enable unimplemented codecs.

    ``None`` means no capability reply, rather than a negative hardware claim.
    Serial bytes are kept intact because some raster recipes depend on them.
    """

    serial_number: bytes = b""
    chip_type: int | None = None
    reported_supports_gray: bool | None = None
    reported_gray_levels: int | None = None
    double_dpi: bool | None = None
    charging_print_restricted: bool | None = None
    multiple_densities: bool | None = None
    label_workshop: bool | None = None
    paper_sensor_suppression: bool | None = None


@dataclass(frozen=True)
class PhomemoPageStep(ProtocolStep):
    """A raster page with its media and completion policy, without transport."""

    paper_mode: PaperMode = PaperMode.PLAIN
    paginated: bool = False
    wait_for_result: bool = True
    completion_delay_sec: float | None = None
    buffered: bool = False
