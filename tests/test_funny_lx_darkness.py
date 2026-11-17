"""Normalized darkness spans the documented LX wire range without raster changes."""
import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PrinterProtocol
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


@pytest.mark.parametrize("level,code", [(-1, 0), (0, 0), (1, 0), (2, 2), (3, 3), (4, 5), (5, 6), (6, 6)])
def test_darkness_uses_full_range_and_preserves_page_bytes(level, code):
    protocol = PrinterProtocol(PrinterCatalog.load().device_from_model("funny_lx_d"))
    raster = RasterSet.from_single(RasterBuffer([1] + [0] * 383, 384, PixelFormat.BW1))
    default = protocol.build_job(raster, is_text=False)
    job = protocol.build_job(raster, is_text=False, blackening=level)
    assert default.steps[0].data == b"\x5a\x0c\x03"
    assert job.steps[0].data == b"\x5a\x0c" + bytes((code,))
    assert job.payload[3:] == default.payload[3:]
    assert [(s.label, s.operation, s.data) for s in job.steps[1:]] == [
        (s.label, s.operation, s.data) for s in default.steps[1:]
    ]
