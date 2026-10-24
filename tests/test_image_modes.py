from dataclasses import asdict, replace
from unittest.mock import patch

import pytest
from PIL import Image

from timiniprint.app.cli import create_print_settings_from_args, parse_args
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.raster_job import build_raster_job
from timiniprint.printing.settings import ImageMode, PrintSettings
from timiniprint.protocol import ImageEncoding, PrinterProtocol, ProtocolFamily
from timiniprint.protocol.families import get_protocol_definition
from timiniprint.protocol.packet import PrefixedPacketStreamDecoder
from timiniprint.protocol.runtime import RuntimePrintCapabilities
from timiniprint.raster import DitherMode, PixelFormat, RasterBuffer, RasterSet


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


@pytest.mark.parametrize("name", ["MX05", "MX06"])
def test_mx05_mx06_default_to_dithered_monochrome_not_bq_gray(catalog, name):
    device = catalog.detect_device(name, "AA:BB:CC:DD:EE:58")
    assert device.model_key == "mx05"
    assert device.protocol_family is ProtocolFamily.V5G
    assert device.profile_key == "v5g_small_203"
    settings = PrintSettings(trim_side_margins=False, trim_top_bottom_margins=False)
    assert settings.available_image_modes(device) == tuple(ImageMode)[1:]
    renderer = DocumentRenderer(image_loader=lambda _: Image.new("RGB", (384, 8), (100, 100, 100)))
    plan = renderer.plan_document(RenderDocument("photo.png"), device, settings)
    page = renderer.print_page(plan, plan.pages[0], device, settings)
    assert page.image_pipeline.default_format is PixelFormat.BW1
    assert page.image_pipeline.encoding is ImageEncoding.V5G_DOT
    assert page.dither_mode is DitherMode.ATKINSON
    raster = page.raster_set.require(PixelFormat.BW1)
    assert 0 < sum(raster.pixels) < len(raster.pixels)
    job = PrinterProtocol(device).build_job(
        page.raster_set, is_text=False, image_pipeline=page.image_pipeline
    )
    opcodes = [
        frame.opcode
        for frame in PrefixedPacketStreamDecoder(ProtocolFamily.V5G).feed(job.payload)
    ]
    assert opcodes.count(0xA2) == raster.height
    assert 0xCF not in opcodes


@pytest.mark.parametrize("profile", ["bq02", "mx02", "mx03", "mx07", "gt02_v5g", "v5g_small_203"])
def test_ordinary_v5g_recipe_does_not_advertise_or_accept_gray(catalog, profile):
    device = catalog.device_from_profile(profile)
    protocol = PrinterProtocol(device)
    assert protocol.supported_pixel_formats() == (PixelFormat.BW1,)
    assert device.image_pipeline.formats == (PixelFormat.BW1,)
    assert PrintSettings().resolve_image_pipeline(device).encoding is ImageEncoding.V5G_DOT
    with pytest.raises(ValueError, match="grayscale"):
        PrintSettings(image_mode=ImageMode.GRAYSCALE).resolve_image_pipeline(device)
    with pytest.raises(ValueError, match="v5g_gray"):
        protocol.resolve_image_pipeline(image_encoding_override=ImageEncoding.V5G_GRAY)


@pytest.mark.parametrize("profile", ["v5x", "v5c"])
def test_default_prefers_grayscale_without_changing_protocol_defaults(catalog, profile):
    device = catalog.device_from_profile("v5x")
    if profile == "v5c":
        device = replace(device, protocol_family=ProtocolFamily.V5C,
            image_pipeline=get_protocol_definition(ProtocolFamily.V5C).behavior.default_image_pipeline)
    settings = PrintSettings()
    assert settings.available_image_modes(device)[0] is ImageMode.GRAYSCALE
    assert settings.resolve_image_pipeline(device).default_format is (PixelFormat.GRAY8 if profile == "v5c" else PixelFormat.GRAY4)
    assert PrinterProtocol(device).resolve_image_pipeline().default_format is PixelFormat.BW1
    assert settings.image_mode is None


def test_negative_session_capability_removes_gray_and_keeps_mono_fallback(catalog):
    device = catalog.device_from_profile("v5x")
    caps = RuntimePrintCapabilities(supports_gray=False)
    settings = PrintSettings()
    assert settings.available_image_modes(device, runtime_capabilities=caps) == tuple(ImageMode)[1:]
    assert settings.resolve_image_pipeline(device, runtime_capabilities=caps).default_format is PixelFormat.BW1
    for override in (None, ImageEncoding.V5X_GRAY):
        for mode in (None, ImageMode.GRAYSCALE):
            choice = PrintSettings(image_mode=mode, image_encoding_override=override)
            assert choice.available_image_modes(device, runtime_capabilities=caps)[0] is ImageMode.ATKINSON
            assert choice.resolve_image_pipeline(device, runtime_capabilities=caps).encoding is ImageEncoding.V5X_DOT


@pytest.mark.parametrize("mode", list(ImageMode))
def test_single_mode_controls_both_preview_and_print(catalog, mode):
    device = catalog.device_from_profile("v5x")
    renderer = DocumentRenderer(image_loader=lambda _: Image.new("RGB", (384, 3), (100, 100, 100)))
    settings = PrintSettings(image_mode=mode.value, trim_side_margins=False, trim_top_bottom_margins=False)
    plan = renderer.plan_document(RenderDocument("photo.png"), device, settings)
    page = renderer.print_page(plan, plan.pages[0], device, settings)
    expected_format = PixelFormat.GRAY4 if mode is ImageMode.GRAYSCALE else PixelFormat.BW1
    assert page.image_pipeline.default_format is expected_format
    assert page.dither_mode is (DitherMode.NONE if mode is ImageMode.GRAYSCALE else mode.dither_mode)
    preview = renderer.preview_page(plan, plan.pages[0], device, settings)
    assert (preview.raster_width, preview.raster_height) == (384, 3)
    with patch("timiniprint.printing.builder.os.path.isfile", return_value=True):
        builder = PrintJobBuilder(device, settings, document_renderer=renderer)
        whole = builder.build_from_file("photo.png")
        streamed = list(builder.iter_page_jobs("photo.png"))
    assert streamed[0].image_pipeline == page.image_pipeline
    assert streamed[0].job.payload == whole.payload
    assert asdict(settings)["image_mode"] == mode.value


def test_prepared_monochrome_raster_is_not_promoted_to_grayscale(catalog):
    device = catalog.device_from_profile("v5x")
    raster = RasterSet({PixelFormat.BW1: RasterBuffer([0] * 384, 384, PixelFormat.BW1)})
    default = build_raster_job(device, raster, is_text=False)
    explicit = build_raster_job(device, raster, is_text=False, settings=PrintSettings(image_mode="threshold"))
    assert default.payload == explicit.payload
    assert default.steps == explicit.steps
    with pytest.raises(ValueError, match="grayscale"):
        build_raster_job(device, raster, is_text=False, settings=PrintSettings(image_mode="grayscale"))


def test_prepared_gray_raster_keeps_its_input_depth(catalog):
    device = catalog.device_from_profile("v5x")
    settings = PrintSettings(image_mode="grayscale")
    pipeline = settings.resolve_image_pipeline(device, raster_formats=(PixelFormat.GRAY8,))
    assert pipeline.default_format is PixelFormat.GRAY8


def test_invalid_mode_and_monochrome_only_codec(catalog):
    with pytest.raises(ValueError):
        PrintSettings(image_mode="automatic")
    device = catalog.device_from_profile("v5x")
    settings = PrintSettings(image_encoding_override=ImageEncoding.V5X_DOT)
    assert ImageMode.GRAYSCALE not in settings.available_image_modes(device)
    settings.image_mode = ImageMode.GRAYSCALE
    with pytest.raises(ValueError, match="grayscale"):
        settings.resolve_image_pipeline(device)


@pytest.mark.parametrize("mode", [None, *ImageMode])
def test_cli_uses_the_same_high_level_mode(mode):
    argv = ["photo.png"] + ([] if mode is None else ["--image-mode", mode.value])
    assert create_print_settings_from_args(parse_args(argv)).image_mode is mode
