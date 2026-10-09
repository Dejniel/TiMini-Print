import zlib

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import ImageEncoding, PaperMode, PrinterProtocol
from timiniprint.protocol.families.bitmap import build_zlib_raster_frame
from timiniprint.protocol.families._tspl import zlib_bitmap_command
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


def asymmetric_raster():
    return RasterSet.from_single(RasterBuffer([1] + [0] * 16 + [1], 9, PixelFormat.BW1))


def test_raw_tspl_padding_and_row_order_match_byte_vector(catalog):
    protocol = PrinterProtocol(catalog.device_from_profile("toprint_tspl_p1"))
    job = protocol.build_job(asymmetric_raster(), is_text=False)
    assert b"BITMAP 0,0,2,2,0,\x7f\xff\xff\x7f\r\n\r\nPRINT 1,1\r\n" in job.payload
    assert protocol.resolve_image_pipeline().encoding is ImageEncoding.TOPRINT_TSPL_BITMAP


def test_raw_esc_padding_and_row_order_match_byte_vector(catalog):
    protocol = PrinterProtocol(catalog.device_from_profile("toprint_hprt_esc_zl1"))
    job = protocol.build_job(asymmetric_raster(), is_text=False)
    assert b"\x1d\x76\x30\0\x02\0\x02\0\x80\0\0\x80" in job.payload
    assert protocol.resolve_image_pipeline().encoding is ImageEncoding.TOPRINT_HPRT_ESC_RASTER


def test_tspl_mode3_keeps_full_zlib_stream_and_double_crlf(catalog):
    protocol = PrinterProtocol(catalog.device_from_profile("toprint_tspl_p1"))
    job = protocol.build_job(asymmetric_raster(), is_text=False,
                             image_encoding_override=ImageEncoding.TOPRINT_TSPL_ZLIB)
    head = b"BITMAP 0,0,2,2,3,"
    size, tail = job.payload.split(head, 1)[1].split(b",", 1)
    compressed, rest = tail[:int(size)], tail[int(size):]
    assert len(compressed) == int(size)
    assert compressed[:2] == b"\x28\x91"
    assert zlib.decompress(compressed, wbits=10) == b"\x80\0\0\x80"
    assert rest == b"\r\n\r\nPRINT 1,1\r\n"


def test_esc_compression_strips_header_only_and_counts_retained_checksum(catalog):
    protocol = PrinterProtocol(catalog.device_from_profile("toprint_hprt_esc_zl1"))
    job = protocol.build_job(asymmetric_raster(), is_text=False,
                             image_encoding_override=ImageEncoding.TOPRINT_HPRT_ESC_ZLIB)
    body = job.steps[1].data
    assert body[:21] == b"\x10\xff\xfe\x01" + bytes(12) + b"\x1b\x61\x01\x1f\0"
    assert body[21:25] == b"\0\x02\0\x02"
    size = int.from_bytes(body[25:29], "big")
    compressed = body[29:29 + size]
    raw = b"\x80\0\0\x80"
    assert zlib.decompress(b"\x28\x91" + compressed, wbits=10) == raw
    assert compressed[-4:] == zlib.adler32(raw).to_bytes(4, "big")
    assert body[29 + size:] == b"\x1d\x0c\x10\xff\xfe\x45\x10\xff\x10\0\x01"


@pytest.mark.parametrize("headerless", [False, True])
def test_shared_zlib_frame_retains_default_bytes_and_checksum(headerless):
    raster = asymmetric_raster().require(PixelFormat.BW1)
    compressor = zlib.compressobj(level=-1, wbits=10, memLevel=8)
    whole = compressor.compress(b"\x80\0\0\x80") + compressor.flush()
    expected = whole[2:] if headerless else whole
    frame = build_zlib_raster_frame(raster, command=b"\x1f\0", level=-1,
                                   omit_zlib_header=headerless)
    assert frame == b"\x1f\0\0\x02\0\x02" + len(expected).to_bytes(4, "big") + expected


def test_shared_tspl_mode3_keeps_eleph_defaults():
    raster = asymmetric_raster().require(PixelFormat.BW1)
    compressor = zlib.compressobj(level=6, wbits=15, memLevel=8)
    compressed = compressor.compress(b"\x80\0\0\x80") + compressor.flush()
    assert zlib_bitmap_command(raster, line_end=b"\n") == (
        f"BITMAP 0,0,2,2,3,{len(compressed)},".encode() + compressed + b"\n"
    )


@pytest.mark.parametrize("encoding", [ImageEncoding.TOPRINT_HPRT_ESC_RASTER, ImageEncoding.TOPRINT_HPRT_ESC_ZLIB])
def test_esc_dimensions_do_not_silently_wrap(catalog, encoding):
    raster = RasterSet.from_single(RasterBuffer([0] * 65536, 1, PixelFormat.BW1))
    protocol = PrinterProtocol(catalog.device_from_profile("toprint_hprt_esc_zl1"))
    with pytest.raises(ValueError, match="dimensions"):
        protocol.build_job(raster, is_text=False, image_encoding_override=encoding)


@pytest.mark.parametrize("key,width,modes", [
    ("toprint_hprt_esc_p11", 88, (PaperMode.TAG, PaperMode.BLACK_TAG)),
    ("toprint_hprt_esc_yhk", 376, (PaperMode.PLAIN,)),
])
def test_screen_geometry_is_paper_data_not_another_protocol(catalog, key, width, modes):
    device = catalog.device_from_model(key)
    assert device.protocol_variant == "zl1"
    assert tuple(p.paper_mode for p in device.profile.paper_presets) == modes
    assert all(p.paper_width_px == p.render_width_px == width for p in device.profile.paper_presets)
    assert all(p.rotation_degrees == 0 and not p.mirror_horizontal for p in device.profile.paper_presets)
    assert device.profile.use_spp
    assert device.profile.stream.chunk_size == 1024 and device.profile.stream.delay_ms == 0
    job = PrinterProtocol(device).build_job(
        RasterSet.from_single(RasterBuffer([1] + [0] * (width - 1), width, PixelFormat.BW1)),
        is_text=False,
    )
    assert job.payload.startswith(b"\x10\xff\x10\x03" + bytes((1 if modes == (PaperMode.PLAIN,) else 2,)))
    assert b"\x1d\x76\x30\0" + (width // 8).to_bytes(2, "little") + b"\x01\0" in job.payload


@pytest.mark.parametrize("key,token", [
    ("toprint_tspl_p1", "P1"), ("toprint_hprt_esc_p11", "P11"),
    ("toprint_hprt_esc_zl1", "P2"), ("toprint_hprt_esc_zl1", "P5"),
    ("toprint_hprt_esc_yhk", "YHK"),
])
def test_to_print_contains_rules_preserve_case_and_reject_le(catalog, key, token):
    model = catalog.require_model(key)
    detection = model.detections[0]
    for name in (token, f"prefix {token} suffix", f"  {token}  "):
        assert detection.matched_specificity(name, None, whitespace_mode=model.whitespace_mode) is not None
    for name in (token.lower(), f"{token}BLE", f"xLE{token}"):
        assert detection.matched_specificity(name, None, whitespace_mode=model.whitespace_mode,
                                           case_sensitive=False) is None


def test_p11_never_enters_p1_tspl_rule(catalog):
    model = catalog.require_model("toprint_tspl_p1")
    for name in ("P11", "a P11 b", "P1-P2", "P1P3", "P1YHK"):
        assert model.detections[0].matched_specificity(name, None, whitespace_mode=model.whitespace_mode) is None


def test_p3_gt08_gw08_do_not_claim_a_recovered_toprint_recipe(catalog):
    assert catalog.get_model("toprint_tspl_gt08") is None
    for token in ("P3", "GT08", "GW08"):
        unsupported = catalog.require_unsupported_model("unsupported_toprint_" + token.lower())
        assert unsupported.origin_ids == ("com.fyhd.toprint",)
        assert unsupported.notes
        assert all(not (m.model.origin_ids == ("com.fyhd.toprint",) and hasattr(m, "profile"))
                   for m in catalog.detect_model(token))


def test_p1_does_not_advertise_unestablished_manual_motion(catalog):
    protocol = PrinterProtocol(catalog.device_from_profile("toprint_tspl_p1"))
    for direction in ("feed", "retract"):
        with pytest.raises(NotImplementedError):
            protocol.build_paper_motion(direction)
