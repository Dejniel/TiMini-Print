from dataclasses import replace

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import ImageEncoding, ImagePipelineConfig, PrinterProtocol
from timiniprint.protocol.runtime import RuntimePrintCapabilities
from timiniprint.raster import PixelFormat


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


@pytest.mark.parametrize("profile,gray,mono", [
    ("v5x", ImageEncoding.V5X_GRAY, ImageEncoding.V5X_DOT),
    ("luck_ppa2l", ImageEncoding.LUCK_NORMAL_GRAY, ImageEncoding.LUCK_NORMAL_RAW),
])
@pytest.mark.parametrize("supported", [None, True, False])
def test_runtime_gray_constraint_uses_declared_codecs(catalog, profile, gray, mono, supported):
    protocol = PrinterProtocol(catalog.device_from_profile(profile))
    caps = RuntimePrintCapabilities(supports_gray=supported)
    resolved = protocol.resolve_image_pipeline(
        pixel_format_override=PixelFormat.GRAY4, runtime_capabilities=caps,
    )
    assert resolved.default_format == (PixelFormat.BW1 if supported is False else PixelFormat.GRAY4)
    assert resolved.encoding == (mono if supported is False else gray)
    choices = protocol.supported_pixel_formats(runtime_capabilities=caps)
    assert (PixelFormat.GRAY4 in choices) == (supported is not False)
    assert (PixelFormat.GRAY8 in choices) == (supported is not False)
    if supported is False:
        assert resolved.formats == (PixelFormat.BW1,)


def test_runtime_gray_constraint_does_not_change_a_selected_mono_codec(catalog):
    protocol = PrinterProtocol(catalog.device_from_profile("luck_ppa2l"))
    resolved = protocol.resolve_image_pipeline(
        image_encoding_override=ImageEncoding.LUCK_NORMAL_COMPRESSED,
        runtime_capabilities=RuntimePrintCapabilities(supports_gray=False),
    )
    assert resolved.encoding is ImageEncoding.LUCK_NORMAL_COMPRESSED
    assert resolved.formats == (PixelFormat.BW1,)


@pytest.mark.parametrize("combined", [False, True])
def test_runtime_gray_constraint_never_invents_a_fallback(catalog, monkeypatch, combined):
    from timiniprint.protocol import job
    protocol = PrinterProtocol(catalog.device_from_profile("v5x"))
    behavior = job.get_protocol_behavior(protocol.device.protocol_family)
    formats = (PixelFormat.BW1, PixelFormat.GRAY4) if combined else (PixelFormat.GRAY4,)
    behavior = replace(behavior, image_encoding_support_resolver=None,
                       image_encoding_support={ImageEncoding.V5X_GRAY: formats})
    monkeypatch.setattr(job, "get_protocol_behavior", lambda _family: behavior)
    kwargs = dict(image_pipeline=ImagePipelineConfig(formats=(PixelFormat.GRAY4,), encoding=ImageEncoding.V5X_GRAY),
                  runtime_capabilities=RuntimePrintCapabilities(supports_gray=False))
    if combined:
        resolved = protocol.resolve_image_pipeline(**kwargs)
        assert resolved.encoding is ImageEncoding.V5X_GRAY and resolved.formats == (PixelFormat.BW1,)
    else:
        with pytest.raises(ValueError, match="no monochrome fallback"):
            protocol.resolve_image_pipeline(**kwargs)
