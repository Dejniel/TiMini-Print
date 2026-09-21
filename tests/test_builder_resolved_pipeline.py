from unittest.mock import patch

from PIL import Image
import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.document_renderer import DocumentRenderer
from timiniprint.printing.settings import ImageMode, PrintSettings
from timiniprint.protocol import ImageEncoding
from timiniprint.protocol.runtime import RuntimePrintCapabilities
from timiniprint.raster import PixelFormat


@pytest.mark.parametrize("explicit_codec", [False, True])
@pytest.mark.parametrize("gray_supported", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_rendered_pipeline_is_not_overridden_again(explicit_codec, gray_supported, streaming):
    device = PrinterCatalog.load().device_from_profile("v5x")
    image = Image.new("RGB", (384, 2), (96, 96, 96))
    caps = RuntimePrintCapabilities(supports_gray=gray_supported)
    common = dict(trim_side_margins=False,
                  trim_top_bottom_margins=False, feed_padding=0)
    settings = PrintSettings(**common, image_mode=ImageMode.GRAYSCALE,
                             image_encoding_override=ImageEncoding.V5X_GRAY if explicit_codec else None)
    baseline = PrintSettings(**common, image_mode=ImageMode.GRAYSCALE if gray_supported else ImageMode.ATKINSON)

    def build(selected):
        builder = PrintJobBuilder(device, selected, runtime_capabilities=caps,
            document_renderer=DocumentRenderer(image_loader=lambda _: image.copy()))
        with patch("timiniprint.printing.builder.os.path.isfile", return_value=True):
            if streaming:
                pages = list(builder.iter_page_jobs("image.png"))
                assert len(pages) == 1
                assert pages[0].image_pipeline.default_format is (PixelFormat.GRAY4 if gray_supported else PixelFormat.BW1)
                return pages[0].job
            return builder.build_from_file("image.png")

    actual, expected = build(settings), build(baseline)
    assert actual.payload == expected.payload
    assert actual.steps == expected.steps
    assert actual.wait_for_completion == expected.wait_for_completion
    assert settings.image_mode is ImageMode.GRAYSCALE
    assert settings.image_encoding_override is (ImageEncoding.V5X_GRAY if explicit_codec else None)
