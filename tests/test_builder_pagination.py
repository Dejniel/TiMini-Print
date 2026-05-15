"""Tests for the per-job-row pagination in PrintJobBuilder.

When `TIMINI_PRINT_MAX_JOB_ROWS` is set and a rendered page is taller than
the limit, the builder must split the raster vertically into sub-rasters
each within the ceiling. We exercise the splitter directly with crafted
RasterSets to keep the test hardware-independent.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from timiniprint.printing.builder import PrintJobBuilder, _resolve_max_job_rows
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class ResolveMaxJobRowsTests(unittest.TestCase):
    def test_unset_returns_zero(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TIMINI_PRINT_MAX_JOB_ROWS", None)
            self.assertEqual(_resolve_max_job_rows(), 0)

    def test_positive_value(self) -> None:
        with mock.patch.dict(os.environ, {"TIMINI_PRINT_MAX_JOB_ROWS": "200"}):
            self.assertEqual(_resolve_max_job_rows(), 200)

    def test_zero_disables_split(self) -> None:
        with mock.patch.dict(os.environ, {"TIMINI_PRINT_MAX_JOB_ROWS": "0"}):
            self.assertEqual(_resolve_max_job_rows(), 0)

    def test_negative_disables_split(self) -> None:
        with mock.patch.dict(os.environ, {"TIMINI_PRINT_MAX_JOB_ROWS": "-1"}):
            self.assertEqual(_resolve_max_job_rows(), 0)

    def test_non_numeric_disables_split(self) -> None:
        with mock.patch.dict(os.environ, {"TIMINI_PRINT_MAX_JOB_ROWS": "lots"}):
            self.assertEqual(_resolve_max_job_rows(), 0)


def _bw1_raster(width: int, height: int) -> RasterSet:
    pixels = [0] * (width * height)
    return RasterSet.from_single(
        RasterBuffer(pixels=pixels, width=width, pixel_format=PixelFormat.BW1)
    )


class _SplitterOnlyBuilder(PrintJobBuilder):
    """Subclass we instantiate without the full device wiring; we only need
    the unbound `_split_raster_for_max_rows` method, so we cheat past the
    parent constructor."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        pass


class SplitRasterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = _SplitterOnlyBuilder()

    def test_no_split_when_max_is_zero(self) -> None:
        raster_set = _bw1_raster(width=8, height=500)
        segments = list(self.builder._split_raster_for_max_rows(raster_set, 0))
        self.assertEqual(len(segments), 1)
        self.assertIs(segments[0], raster_set)

    def test_no_split_when_under_limit(self) -> None:
        raster_set = _bw1_raster(width=8, height=100)
        segments = list(self.builder._split_raster_for_max_rows(raster_set, 200))
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].height, 100)

    def test_exact_multiple_of_limit(self) -> None:
        raster_set = _bw1_raster(width=8, height=400)
        segments = list(self.builder._split_raster_for_max_rows(raster_set, 200))
        self.assertEqual([s.height for s in segments], [200, 200])

    def test_ragged_tail_segment(self) -> None:
        raster_set = _bw1_raster(width=8, height=450)
        segments = list(self.builder._split_raster_for_max_rows(raster_set, 200))
        self.assertEqual([s.height for s in segments], [200, 200, 50])

    def test_preserves_width_and_pixel_format(self) -> None:
        raster_set = _bw1_raster(width=384, height=300)
        segments = list(self.builder._split_raster_for_max_rows(raster_set, 100))
        for segment in segments:
            self.assertEqual(segment.width, 384)
            for raster in segment.rasters.values():
                self.assertEqual(raster.pixel_format, PixelFormat.BW1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
