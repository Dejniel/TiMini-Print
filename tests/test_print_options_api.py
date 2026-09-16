from dataclasses import replace

import pytest

from timiniprint.devices.profiles import LevelProfile, ModeLevelProfile

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import ImageEncoding, PrinterProtocol
from timiniprint.protocol.runtime import RuntimePrintCapabilities
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


@pytest.fixture
def protocol():
    return PrinterProtocol(PrinterCatalog.load().device_from_profile("v5x"))


def test_pixel_format_selects_a_matching_codec_without_requiring_its_name(protocol):
    pipeline = protocol.resolve_image_pipeline(pixel_format_override=PixelFormat.GRAY4)
    assert pipeline.default_format is PixelFormat.GRAY4
    assert pipeline.encoding is ImageEncoding.V5X_GRAY
    assert protocol.resolve_image_pipeline().encoding is ImageEncoding.V5X_DOT
    with pytest.raises(ValueError, match="does not support gray4"):
        protocol.resolve_image_pipeline(
            pixel_format_override=PixelFormat.GRAY4, image_encoding_override=ImageEncoding.V5X_DOT,
        )


def test_automatic_codec_choice_builds_the_same_job_as_explicit_choice(protocol):
    raster = RasterSet.from_single(RasterBuffer([15, 0] * 384, 384, PixelFormat.GRAY4))
    automatic = protocol.build_job(raster, is_text=False, pixel_format_override=PixelFormat.GRAY4)
    explicit = protocol.build_job(
        raster, is_text=False, pixel_format_override=PixelFormat.GRAY4,
        image_encoding_override=ImageEncoding.V5X_GRAY,
    )
    assert automatic.payload == explicit.payload
    assert automatic.steps == explicit.steps


def test_settings_can_be_restricted_by_live_capabilities(protocol):
    assert "blackening" in protocol.supported_print_settings()
    assert "blackening" not in protocol.supported_print_settings(
        runtime_capabilities=RuntimePrintCapabilities(supports_blackening=False),
    )
    assert set(protocol.supported_pixel_formats()) == {PixelFormat.BW1, PixelFormat.GRAY4, PixelFormat.GRAY8}


def test_paper_motion_query_uses_the_real_builders():
    catalog = PrinterCatalog.load()
    tiny = PrinterProtocol(catalog.device_from_model("pocket_printer"))
    assert tiny.supports_paper_motion("feed")
    assert tiny.supports_paper_motion("retract")
    label = PrinterProtocol(catalog.device_from_model("eleph_tspl_p1"))
    assert not label.supports_paper_motion("retract")
    with pytest.raises(ValueError, match="Unknown paper motion"):
        tiny.supports_paper_motion("cut")


def test_catalog_options_are_resolvable_without_a_connection():
    catalog = PrinterCatalog.load()
    for model in catalog.models:
        protocol = PrinterProtocol(catalog.device_from_model(model.model_key))
        for paper in protocol.device.profile.paper_presets:
            for fmt in protocol.supported_pixel_formats(paper_preset_key=paper.key):
                pipeline = protocol.resolve_image_pipeline(pixel_format_override=fmt, paper_preset_key=paper.key)
                assert pipeline.default_format == fmt, (model.model_key, paper.key)
                assert set(protocol.supported_print_settings(
                    pixel_format=fmt, paper_preset_key=paper.key,
                )) <= {"blackening", "text_mode"}
        for action in ("feed", "retract"):
            assert isinstance(protocol.supports_paper_motion(action), bool)


def test_options_reject_unknown_paper(protocol):
    with pytest.raises(ValueError, match="does not support paper"):
        protocol.supported_pixel_formats(paper_preset_key="not-a-paper")


def test_density_control_requires_adjustable_profile_levels():
    device = PrinterCatalog.load().device_from_model("phomemo_m02")
    assert "blackening" in PrinterProtocol(device).supported_print_settings()
    constant = LevelProfile(low=2, middle=2, high=2)
    defaults = replace(device.profile.print_defaults, density=ModeLevelProfile(constant, constant))
    device = replace(device, profile=replace(device.profile, print_defaults=defaults))
    assert "blackening" not in PrinterProtocol(device).supported_print_settings()


def test_recipe_without_density_commands_does_not_advertise_adjustment():
    device = PrinterCatalog.load().device_from_model("printmaster_m110")
    assert PrinterProtocol(device).supported_print_settings() == ()
