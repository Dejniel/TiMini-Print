import pytest

from timiniprint.protocol.families.bitmap import build_esc_star_raster
from timiniprint.raster import PixelFormat, RasterBuffer


@pytest.mark.parametrize("mode,stripes", [(0, 1), (1, 1), (32, 3), (33, 3)])
def test_column_bands_preserve_bit_order_and_pad_last_band(mode, stripes):
    height = stripes * 8 + 1
    pixels = [0] * (2 * height)
    pixels[0] = pixels[15] = pixels[-2] = 1
    raster = RasterBuffer(pixels, 2, PixelFormat.BW1)
    header = b"\x1b\x2a" + bytes([mode]) + b"\x02\x00"
    first = b"\x80" + bytes(stripes - 1) + b"\x01" + bytes(stripes - 1)
    last = b"\x80" + bytes(stripes * 2 - 1)
    assert build_esc_star_raster(raster, mode=mode, band_trailer=b"\n") == (
        header + first + b"\n" + header + last + b"\n"
    )


def test_default_preserves_existing_24_dot_format():
    raster = RasterBuffer([1] * 25, 1, PixelFormat.BW1)
    assert build_esc_star_raster(raster, band_trailer=b"\x1b\x4a\x00\n") == (
        b"\x1b\x2a\x21\x01\x00\xff\xff\xff\x1b\x4a\x00\n"
        b"\x1b\x2a\x21\x01\x00\x80\x00\x00\x1b\x4a\x00\n"
    )


@pytest.mark.parametrize("mode", [-1, 2, 31, 34, 256])
def test_rejects_unknown_mode(mode):
    with pytest.raises(ValueError, match="mode"):
        build_esc_star_raster(RasterBuffer([1], 1, PixelFormat.BW1),
                              mode=mode, band_trailer=b"")


def test_rejects_non_monochrome_input():
    with pytest.raises(ValueError, match="bw1"):
        build_esc_star_raster(RasterBuffer([15], 1, PixelFormat.GRAY4), band_trailer=b"")


def test_rejects_unrepresentable_width():
    with pytest.raises(ValueError, match="width"):
        build_esc_star_raster(RasterBuffer([0] * 65536, 65536, PixelFormat.BW1),
                              band_trailer=b"")
