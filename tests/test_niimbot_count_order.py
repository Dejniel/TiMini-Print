import pytest

from timiniprint.protocol.families.niimbot.core import _NiimbotRowEncoder, parse_packets
from timiniprint.raster import PixelFormat, RasterBuffer


@pytest.mark.parametrize("mode,counts", [
    ("total", b"\0\x60\x03"),
    ("total_be", b"\0\x03\x60"),
    ("regional", b"\x20\x20\x20"),
])
def test_black_count_order_is_explicit(mode, counts):
    raster = RasterBuffer([1] * 864, 864, PixelFormat.BW1)
    row, = _NiimbotRowEncoder(counts_mode=mode, head_width_pixels=864).encode(raster)
    packet, = parse_packets(row[2])
    assert packet.command == 0x85
    assert packet.data[2:5] == counts
    assert packet.data[6:] == bytes([255]) * 108
