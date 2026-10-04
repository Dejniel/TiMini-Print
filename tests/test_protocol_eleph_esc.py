from __future__ import annotations

import zlib
from dataclasses import replace
from unittest.mock import patch

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PrinterProtocol, ProtocolFamily
from timiniprint.protocol.compression import compress_zlib
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


def esc_device():
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    device = replace(device, protocol_family=ProtocolFamily.ELEPH_ESC)
    return device.with_print_profile(
        device.profile, image_pipeline=replace(device.image_pipeline, encoding=ImageEncoding.ELEPH_ESC_RAW),
    )


def asymmetric_raster():
    pixels = [0] * 20
    for x, y in ((0, 0), (2, 0), (8, 0), (3, 1), (9, 1)):
        pixels[y * 10 + x] = 1
    return RasterBuffer(pixels, 10, PixelFormat.BW1)


def test_raw_esc_matches_complete_asymmetric_vector():
    job = PrinterProtocol(esc_device()).build_job(
        RasterSet.from_single(asymmetric_raster()), is_text=False,
    )
    assert job.payload == bytes.fromhex("1b40 1b6101 1d763030 0200 0200 a0801040 1d0c")
    assert not job.steps


def test_compressed_esc_matches_header_and_zlib_parameters():
    with patch("timiniprint.protocol.families.bitmap.compress_zlib", wraps=compress_zlib) as compressor:
        job = PrinterProtocol(esc_device()).build_job(
            RasterSet.from_single(asymmetric_raster()), is_text=False,
            image_encoding_override=ImageEncoding.ELEPH_ESC_ZLIB,
        )
    compressor.assert_called_once_with(
        b"\xa0\x80\x10\x40", window_bits=10, level=-1, memory_level=9,
    )
    assert job.payload[:12] == bytes.fromhex("1b40 1b6101 1b2321 0002 0002")
    size = int.from_bytes(job.payload[12:16], "big")
    compressed = job.payload[16:16 + size]
    assert compressed[0] == 0x28  # 1 KiB zlib window, not raw DEFLATE or gzip.
    assert zlib.decompress(compressed) == b"\xa0\x80\x10\x40"
    assert job.payload[16 + size:] == b"\x1d\x0c"


@pytest.mark.parametrize("width", range(1, 25))
@pytest.mark.parametrize("compressed", [False, True])
def test_esc_white_padding_and_msb_leftmost_pixel(width, compressed):
    pixels = [0] * width
    pixels[0] = pixels[-1] = 1
    raster = RasterBuffer(pixels, width, PixelFormat.BW1)
    job = PrinterProtocol(esc_device()).build_job(
        RasterSet.from_single(raster), is_text=False,
        image_encoding_override=ImageEncoding.ELEPH_ESC_ZLIB if compressed else ImageEncoding.ELEPH_ESC_RAW,
        lsb_first=True,  # This dialect has fixed MSB-first ordering.
    )
    stride = (width + 7) // 8
    if compressed:
        assert job.payload[8:10] == stride.to_bytes(2, "big")
        bitmap = zlib.decompress(job.payload[16:-2])
    else:
        assert job.payload[9:11] == stride.to_bytes(2, "little")
        bitmap = job.payload[13:-2]
    expected = bytearray(stride)
    expected[0] |= 0x80
    expected[(width - 1) // 8] |= 1 << (7 - (width - 1) % 8)
    assert bitmap == expected


def test_compression_failure_does_not_silently_fall_back_to_raw():
    with patch("timiniprint.protocol.families.bitmap.compress_zlib", side_effect=zlib.error("failed")):
        with pytest.raises(zlib.error, match="failed"):
            PrinterProtocol(esc_device()).build_job(
                RasterSet.from_single(asymmetric_raster()), is_text=False,
                image_encoding_override=ImageEncoding.ELEPH_ESC_ZLIB,
            )


def test_oversized_height_is_not_split_into_multiple_form_feeds():
    raster = RasterBuffer([0] * 65536, 1, PixelFormat.BW1)
    with pytest.raises(ValueError, match="16-bit header"):
        PrinterProtocol(esc_device()).build_job(RasterSet.from_single(raster), is_text=False)
