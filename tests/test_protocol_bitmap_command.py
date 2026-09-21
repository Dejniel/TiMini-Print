import pytest

from timiniprint.protocol.families.bitmap import build_gs_v0_blocks
from timiniprint.raster import PixelFormat, RasterBuffer


@pytest.mark.parametrize("command", [0x75, 0x76])
def test_raster_command_applies_to_every_block_without_changing_geometry(command):
    raster = RasterBuffer([1, 0, 1, 0, 1, 0, 1, 0, 0], 3, PixelFormat.BW1)
    actual = build_gs_v0_blocks(raster, command=command, max_lines_per_block=2)
    prefix = bytes((0x1D, command, 0x30, 0))
    assert actual == prefix + bytes.fromhex("0100 0200 a040") + prefix + bytes.fromhex("0100 0100 80")
    if command == 0x76:
        assert actual == build_gs_v0_blocks(raster, max_lines_per_block=2)
