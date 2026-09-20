import pytest

from timiniprint.protocol.families._tspl import bitmap_command
from timiniprint.raster import PixelFormat, RasterBuffer


@pytest.mark.parametrize("invert", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("mode", [0, 1, 2])
def test_tspl_bitmap_mode_polarity_and_traversal(mode, reverse, invert):
    raster = RasterBuffer([1] + [0] * 127, 16, PixelFormat.BW1)
    expected = bytes(15) + b"\1" if reverse else b"\x80" + bytes(15)
    if invert:
        expected = bytes(value ^ 0xFF for value in expected)
    assert bitmap_command(raster, line_end=b"\r\n", mode=mode,
                          reverse_traversal=reverse, invert_bits=invert) == (
        f"BITMAP 0,0,2,8,{mode},".encode() + expected + b"\r\n"
    )


def test_tspl_bitmap_defaults_preserve_existing_bytes():
    raster = RasterBuffer([1] + [0] * 7, 8, PixelFormat.BW1)
    assert bitmap_command(raster, line_end=b"\n") == b"BITMAP 0,0,1,1,0,\x80\n"


@pytest.mark.parametrize("mode", [-1, 3, 4])
def test_uncompressed_tspl_bitmap_rejects_other_modes(mode):
    raster = RasterBuffer([0] * 8, 8, PixelFormat.BW1)
    with pytest.raises(ValueError, match="mode"):
        bitmap_command(raster, line_end=b"\r\n", mode=mode)
