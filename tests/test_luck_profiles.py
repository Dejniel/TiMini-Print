import zlib

import pytest
from PIL import Image

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.settings import ImageMode, PrintSettings
from timiniprint.protocol import ImageEncoding, PaperMode, PrinterProtocol, ProtocolFamily, ProtocolStepOperation
from timiniprint.protocol.runtime import RuntimePrintCapabilities
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


def raster(width=8):
    return RasterSet.from_single(RasterBuffer([1] + [0] * (width - 1), width, PixelFormat.BW1))


@pytest.mark.parametrize("key", ["luck_a2", "luck_a2h", "luck_qirui_q1", "luck_qirui_q2"])
def test_fixed_mono_profiles_cannot_select_gray(catalog, key):
    device = catalog.device_from_profile(key)
    for caps in (None, RuntimePrintCapabilities(supports_gray=True)):
        protocol = PrinterProtocol(device)
        assert protocol.supported_pixel_formats(runtime_capabilities=caps) == (PixelFormat.BW1,)
        assert PrintSettings().resolve_image_pipeline(device, runtime_capabilities=caps).encoding is ImageEncoding.LUCK_NORMAL_RAW
        assert ImageMode.GRAYSCALE not in PrintSettings().available_image_modes(device, runtime_capabilities=caps)
        with pytest.raises(ValueError):
            PrintSettings(image_mode=ImageMode.GRAYSCALE).resolve_image_pipeline(device, runtime_capabilities=caps)
        with pytest.raises(ValueError):
            protocol.resolve_image_pipeline(image_encoding_override=ImageEncoding.LUCK_NORMAL_GRAY, runtime_capabilities=caps)


@pytest.mark.parametrize("key", ["luck_ppa2l", "luck_ppa2lh"])
@pytest.mark.parametrize("gray", [False, True])
def test_gray_probe_still_controls_lujiang_default(catalog, key, gray):
    device = catalog.device_from_profile(key)
    caps = RuntimePrintCapabilities(supports_gray=gray)
    pipeline = PrintSettings().resolve_image_pipeline(device, runtime_capabilities=caps)
    expected = ImageEncoding.LUCK_NORMAL_GRAY if gray else ImageEncoding.LUCK_NORMAL_RAW
    assert pipeline.encoding is expected


def test_all_normal_a4_profiles_have_bounded_geometry_and_no_gray(catalog):
    for profile in catalog.profiles:
        if profile.protocol_default.type is not ProtocolFamily.LUCK_NORMAL_A4:
            continue
        device = catalog.device_from_profile(profile.profile_key)
        protocol = PrinterProtocol(device)
        dpi = profile.dev_dpi
        assert dpi in (203, 300), profile.profile_key
        narrow = device.protocol_variant in ("luckp_a41", "luckp_a42")
        maximum = (2400 if narrow else 2496) if dpi == 300 else (1616 if narrow else 1648)
        sheets = ({"A4 sheet": (2400, 3480), "A5 sheet": (1692, 2400),
                   "Letter sheet": (2496, 3240), "Legal sheet": (2496, 4152)} if dpi == 300 else
                  {"A4 sheet": (1616, 2300), "A5 sheet": (1108, 1616),
                   "Letter sheet": (1648, 2160), "Legal sheet": (1648, 2768)})
        if narrow:
            sheets = {"A4 sheet": sheets["A4 sheet"]}
        assert profile.default_paper_preset.paper_width_px == maximum
        actual_sheets = {}
        for paper in profile.paper_presets:
            assert paper.render_width_px == paper.paper_width_px <= maximum, paper.key
            assert not paper.left_padding_px and not paper.top_padding_px
            assert not paper.rotation_degrees and not paper.mirror_horizontal
            assert protocol.supported_pixel_formats(paper_preset_key=paper.key) == (PixelFormat.BW1,)
            with pytest.raises(ValueError):
                protocol.resolve_image_pipeline(paper_preset_key=paper.key, image_encoding_override=ImageEncoding.LUCK_NORMAL_GRAY)
            assert PrintSettings(paper_preset_key=paper.key).resolve_image_pipeline(device).default_format is PixelFormat.BW1
            if paper.paper_mode is PaperMode.FOLDER:
                actual_sheets[paper.label] = (paper.paper_width_px, paper.render_height_px)
        assert actual_sheets == sheets, profile.profile_key


@pytest.mark.parametrize("key,width", [("luck_a40", 591), ("luck_a49h", 887)])
def test_77mm_roll_keeps_logical_width_and_byte_padding(catalog, key, width):
    device = catalog.device_from_profile(key)
    paper = next(p for p in device.profile.paper_presets if p.label == "77 mm roll")
    assert paper.render_width_px == paper.paper_width_px == width
    settings = PrintSettings(image_mode=ImageMode.THRESHOLD, paper_preset_key=paper.key, trim_side_margins=False, trim_top_bottom_margins=False)
    renderer = DocumentRenderer(image_loader=lambda _: Image.new("RGB", (width, 1), "black"))
    plan = renderer.plan_document(RenderDocument("image.png"), device, settings)
    page = renderer.print_page(plan, plan.pages[0], device, settings)
    assert page.raster_set.require(PixelFormat.BW1).width == width
    black_row = RasterSet.from_single(RasterBuffer([1] * width, width, PixelFormat.BW1))
    job = PrinterProtocol(device).build_job(black_row, is_text=False, paper_preset_key=paper.key)
    width_bytes = (width + 7) // 8
    if key == "luck_a40":
        offset = job.payload.index(b"\x1d\x76\x30\x00")
        assert int.from_bytes(job.payload[offset + 4:offset + 6], "little") == width_bytes
        body = job.payload[offset + 8:offset + 8 + width_bytes]
    else:
        offset = job.payload.index(b"\x1f\x10")
        assert int.from_bytes(job.payload[offset + 2:offset + 4], "big") == width_bytes
        length = int.from_bytes(job.payload[offset + 6:offset + 10], "big")
        body = zlib.decompress(job.payload[offset + 10:offset + 10 + length])
    # Both widths leave seven printable pixels and one white padding bit.
    assert body == b"\xff" * (width_bytes - 1) + b"\xfe"


@pytest.mark.parametrize("key,size", [("luck_a40", (1616, 2300)), ("luck_a49h", (2400, 3480)),
                                    ("luck_a41_luckp", (1616, 2300)), ("luck_a42_luckp", (1616, 2300))])
def test_a4_sheet_render_has_fixed_canvas(catalog, key, size):
    device = catalog.device_from_profile(key)
    paper = next(p for p in device.profile.paper_presets if p.label == "A4 sheet")
    renderer = DocumentRenderer(image_loader=lambda _: Image.new("RGB", (400, 300), "black"))
    settings = PrintSettings(image_mode=ImageMode.THRESHOLD, paper_preset_key=paper.key, trim_side_margins=False, trim_top_bottom_margins=False)
    plan = renderer.plan_document(RenderDocument("image.png"), device, settings)
    preview = renderer.preview_page(plan, plan.pages[0], device, settings)
    assert (preview.raster_width, preview.raster_height) == size


@pytest.mark.parametrize("key,compressed", [("luck_a41_luckp", False), ("luck_a42_luckp", True)])
def test_luckp_default_codec_and_plain_recipe(catalog, key, compressed):
    device = catalog.device_from_profile(key)
    expected = ImageEncoding.LUCK_NORMAL_COMPRESSED if compressed else ImageEncoding.LUCK_NORMAL_RAW
    assert PrintSettings().resolve_image_pipeline(device).encoding is expected
    job = PrinterProtocol(device).build_job(raster(1616), is_text=False)
    assert b"\x1f\x80" not in job.payload
    assert job.payload.endswith(bytes.fromhex("1b 4a 90 10 ff f1 45"))
    if compressed:
        offset = job.payload.index(b"\x1f\x10")
        assert job.payload[offset + 2:offset + 6] == bytes.fromhex("00 ca 00 01")
        length = int.from_bytes(job.payload[offset + 6:offset + 10], "big")
        assert zlib.decompress(job.payload[offset + 10:offset + 10 + length]) == b"\x80" + bytes(201)
    else:
        assert bytes.fromhex("1d 76 30 00 ca 00 01 00") in job.payload


@pytest.mark.parametrize("key", ["luck_qirui_q1", "luck_qirui_q2"])
def test_qirui_tag_does_not_select_a2_paper(catalog, key):
    protocol = PrinterProtocol(catalog.device_from_profile(key))
    for index in (1, 2):
        job = protocol.build_job(raster(), is_text=False, paper_mode=PaperMode.TAG, page_index=index, page_count=2)
        assert bytes.fromhex("10 ff f1 02") in job.payload
        assert b"\x1f\x80" not in job.payload
        assert job.payload.endswith(bytes.fromhex("1d 0c 10 ff f1 45"))
        assert all(step.label != "paper type" for step in job.steps)


def test_a2_tag_still_selects_paper_without_query(catalog):
    job = PrinterProtocol(catalog.device_from_profile("luck_a2")).build_job(raster(), is_text=False, paper_mode=PaperMode.TAG)
    assert bytes.fromhex("1f 80 01 20") in job.payload
    assert next(step for step in job.steps if step.label == "paper type").operation is ProtocolStepOperation.SEND


def test_ppa2lh_feed_matches_120_dot_motion(catalog):
    protocol = PrinterProtocol(catalog.device_from_profile("luck_ppa2lh"))
    job = protocol.build_job(raster(), is_text=False, paper_mode=PaperMode.PLAIN)
    assert next(s.data for s in job.steps if s.label == "line feed") == bytes.fromhex("1b 4a 78")
    assert job.payload.endswith(bytes.fromhex("1b 4a 78 1b bb bb 10 ff f1 45"))


@pytest.mark.parametrize("name,middle", [("APA41_1234", 2), ("APA49_1234", 3), ("E49_1234", 3),
                                       ("APA49H_1234", 3), ("TPA46Pro_1234", 2), ("DP_ITP06_1234", 2)])
def test_dense_profiles_keep_individual_density_defaults(catalog, name, middle):
    device = catalog.detect_device(name)
    levels = device.profile.density.image
    assert (levels.low, levels.middle, levels.high) == (0, middle, 5)
    job = PrinterProtocol(device).build_job(raster(), is_text=False)
    assert job.payload.startswith(bytes.fromhex("10 ff 10 00") + bytes([middle]))
