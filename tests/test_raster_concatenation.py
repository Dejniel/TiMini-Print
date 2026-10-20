import pytest

from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


def test_vertical_concatenation_preserves_rows_and_formats():
    first = RasterSet({
        PixelFormat.BW1: RasterBuffer([1, 0], 2, PixelFormat.BW1),
        PixelFormat.GRAY8: RasterBuffer([0, 255], 2, PixelFormat.GRAY8),
    })
    second = RasterSet({
        PixelFormat.GRAY8: RasterBuffer([100, 200, 255, 0], 2, PixelFormat.GRAY8),
        PixelFormat.BW1: RasterBuffer([1, 1, 0, 1], 2, PixelFormat.BW1),
    })
    combined = RasterSet.concatenate_vertical((first, second))
    assert (combined.width, combined.height) == (2, 3)
    assert list(combined.require(PixelFormat.BW1).pixels) == [1, 0, 1, 1, 0, 1]
    assert list(combined.require(PixelFormat.GRAY8).pixels) == [0, 255, 100, 200, 255, 0]
    assert first.height == 1
    assert second.height == 2


@pytest.mark.parametrize("items", [
    (),
    (RasterSet.from_single(RasterBuffer([1, 0], 2)), RasterSet.from_single(RasterBuffer([1], 1))),
    (RasterSet.from_single(RasterBuffer([1], 1)), RasterSet.from_single(RasterBuffer([100], 1, PixelFormat.GRAY8))),
    (RasterSet({}),),
])
def test_vertical_concatenation_rejects_incompatible_rasters(items):
    with pytest.raises(ValueError):
        RasterSet.concatenate_vertical(items)
