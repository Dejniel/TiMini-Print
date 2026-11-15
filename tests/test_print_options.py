"""One pure option resolver serves offline choices and prepared sessions."""
from dataclasses import replace

import pytest

from timiniprint import printing
from timiniprint.devices import PrinterCatalog
from timiniprint.printing import describe_print_options
from timiniprint.printing.connected import ConnectedPrinter
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.settings import ImageMode, PrintQuality, PrintSettings
from timiniprint.protocol.runtime import RuntimePrintCapabilities
from timiniprint.protocol import ImageEncoding
from timiniprint.protocol.families import get_protocol_behavior


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


@pytest.mark.parametrize("profile", ["x6h", "v5g_small_203", "v5x"])
@pytest.mark.parametrize("gray", [None, False, True])
def test_session_and_offline_resolver_agree_without_io_or_settings_mutation(catalog, profile, gray):
    device = catalog.device_from_profile(profile)
    settings = PrintSettings(image_mode=ImageMode.ATKINSON)
    before = replace(settings)
    caps = RuntimePrintCapabilities(supports_gray=gray)
    offline = describe_print_options(device, settings, runtime_capabilities=caps)
    connected = ConnectedPrinter(object(), PreparedPrinter(device, capabilities=caps))
    assert connected.print_options(settings) == offline
    assert settings == before
    assert offline.image_mode in offline.image_modes
    assert offline.print_quality in offline.print_qualities
    assert offline.paper_preset_key in {paper.key for paper in offline.paper_presets}
    if gray is False:
        assert ImageMode.GRAYSCALE not in offline.image_modes


def test_an_old_gray_selection_is_replaced_only_in_the_description(catalog):
    device = catalog.device_from_profile("v5x")
    settings = PrintSettings(image_mode=ImageMode.GRAYSCALE)
    options = describe_print_options(device, settings, runtime_capabilities=RuntimePrintCapabilities(supports_gray=False))
    assert options.image_mode is ImageMode.ATKINSON
    assert settings.image_mode is ImageMode.GRAYSCALE
    assert PrintQuality.STANDARD in options.print_qualities


def test_a_known_negative_density_capability_hides_only_that_control(catalog):
    device = catalog.device_from_profile("v5x")
    normal = describe_print_options(device)
    negative = describe_print_options(device, runtime_capabilities=RuntimePrintCapabilities(supports_blackening=False))
    assert not negative.blackening_levels
    assert negative.image_modes == normal.image_modes


def test_invalid_paper_is_not_silently_replaced_with_the_default(catalog):
    device = catalog.device_from_profile("v5x")
    with pytest.raises(ValueError):
        describe_print_options(device, PrintSettings(paper_preset_key="not-a-paper"))


def test_controls_follow_selected_codec_not_the_profiles_default(catalog, monkeypatch):
    device = catalog.device_from_profile("v5x")
    behavior = replace(get_protocol_behavior(device.protocol_family), print_controls_resolver=lambda *args:
                       ("text_mode",) if args[1] is ImageEncoding.V5X_GRAY else ("blackening",))
    monkeypatch.setattr("timiniprint.protocol.job.get_protocol_behavior", lambda _: behavior)
    gray = describe_print_options(device, PrintSettings(image_mode=ImageMode.GRAYSCALE))
    mono = describe_print_options(device, PrintSettings(image_mode=ImageMode.ATKINSON))
    assert gray.text_mode_available and not gray.blackening_levels
    assert not mono.text_mode_available and mono.blackening_levels


def test_every_catalog_model_has_resolvable_offline_user_options(catalog):
    for model in catalog.models:
        device = catalog.device_from_model(model.model_key)
        options = describe_print_options(device)
        assert options.image_mode in options.image_modes, model.model_key
        assert options.print_quality in options.print_qualities, model.model_key


def test_print_options_do_not_expose_device_configuration_or_new_commands(catalog):
    device = catalog.device_from_profile("v5x")
    printer = ConnectedPrinter(object(), PreparedPrinter(device))
    assert printer.print_options() == describe_print_options(device)
    for name in ("device_properties", "read_device_values", "set_device_value",
                 "device_operations", "calibrate_paper", "pause", "resume"):
        assert not hasattr(printer, name)
    for name in ("DeviceProperty", "DeviceReadResult", "DeviceWriteResult", "DeviceOperation"):
        assert not hasattr(printing, name)


def test_options_offer_only_implemented_print_controls(catalog):
    options = describe_print_options(catalog.device_from_profile("v5x"))
    assert options.print_qualities == (PrintQuality.STANDARD,)
    assert options.anti_crease_modes == options.material_presets == ()
    assert options.page_confirmation.value == "unavailable"
    assert not options.copies_available
    assert not options.copy_gap_available
    for quality in (PrintQuality.SPEED, PrintQuality.HIGH):
        with pytest.raises(ValueError, match="only standard print quality"):
            PrintSettings(print_quality=quality)
