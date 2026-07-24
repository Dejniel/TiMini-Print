from __future__ import annotations

import subprocess
import sys
import unittest
from dataclasses import replace
from unittest.mock import patch

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.raster_job import build_raster_job
from timiniprint.printing.runtime.base import PreparedRuntimeContext
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import PageFlow
from timiniprint.protocol.runtime import RuntimePrintCapabilities
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class RasterJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = PrinterCatalog.load().device_from_profile("d1")
        self.raster = RasterSet.from_single(
            RasterBuffer(
                pixels=[0] * 8,
                width=8,
                pixel_format=PixelFormat.BW1,
            )
        )

    def test_build_raster_job_uses_print_settings_defaults(self) -> None:
        with patch(
            "timiniprint.protocol.job._build_job_model_from_raster_set",
            return_value=(b"A", ()),
        ) as build_job_mock:
            job = build_raster_job(self.device, self.raster, is_text=True)

        self.assertEqual(job.payload, b"A")
        self.assertEqual(build_job_mock.call_args.kwargs["feed_padding"], 12)
        self.assertEqual(build_job_mock.call_args.kwargs["blackening"], 3)
        self.assertTrue(build_job_mock.call_args.kwargs["is_text"])
        self.assertEqual(build_job_mock.call_args.kwargs["page_flow"], PageFlow.PAGED)

    def test_build_raster_job_accepts_settings_and_runtime_context(self) -> None:
        capabilities = RuntimePrintCapabilities(supports_gray=False)
        context = PreparedRuntimeContext(capabilities=capabilities)
        settings = PrintSettings(feed_padding=7, blackening=5)

        with patch(
            "timiniprint.protocol.job._build_job_model_from_raster_set",
            return_value=(b"B", ()),
        ) as build_job_mock:
            job = build_raster_job(
                self.device,
                self.raster,
                is_text=False,
                settings=settings,
                runtime_context=context,
            )

        self.assertTrue(job.wait_for_completion)
        self.assertEqual(build_job_mock.call_args.kwargs["feed_padding"], 7)
        self.assertEqual(build_job_mock.call_args.kwargs["blackening"], 5)
        self.assertFalse(build_job_mock.call_args.kwargs["is_text"])
        self.assertIs(
            build_job_mock.call_args.kwargs["runtime_capabilities"],
            capabilities,
        )

    def test_build_raster_job_centers_raw_raster_on_wider_paper(self) -> None:
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    paper_width_px=16,
                    render_width_px=8,
                    left_padding_px=0,
                ),
            ),
        )
        device = replace(self.device, profile=profile)
        raster = RasterSet.from_single(
            RasterBuffer(
                pixels=[1] * 8,
                width=8,
                pixel_format=PixelFormat.BW1,
            )
        )

        with patch(
            "timiniprint.protocol.job._build_job_model_from_raster_set",
            return_value=(b"C", ()),
        ) as build_job_mock:
            job = build_raster_job(device, raster, is_text=False)

        raster_set = build_job_mock.call_args.kwargs["raster_set"]
        bw = raster_set.require(PixelFormat.BW1)
        self.assertEqual(job.payload, b"C")
        self.assertEqual(bw.width, 16)
        self.assertEqual(list(bw.pixels), [0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0])

    def test_build_raster_job_keeps_protocol_left_padding_out_of_raster(self) -> None:
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    paper_width_px=16,
                    render_width_px=8,
                    left_padding_px=8,
                ),
            ),
        )
        device = replace(self.device, profile=profile)

        with patch(
            "timiniprint.protocol.job._build_job_model_from_raster_set",
            return_value=(b"D", ()),
        ) as build_job_mock:
            job = build_raster_job(device, self.raster, is_text=False)

        raster_set = build_job_mock.call_args.kwargs["raster_set"]
        self.assertEqual(job.payload, b"D")
        self.assertEqual(raster_set.width, 8)

    def test_build_raster_job_pads_to_exact_raster_height(self) -> None:
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    paper_width_px=8,
                    render_width_px=8,
                    raster_height_px=3,
                ),
            ),
        )
        device = replace(self.device, profile=profile)
        raster = RasterSet.from_single(
            RasterBuffer(
                pixels=[1] * 8,
                width=8,
                pixel_format=PixelFormat.BW1,
            )
        )

        with patch(
            "timiniprint.protocol.job._build_job_model_from_raster_set",
            return_value=(b"E", ()),
        ) as build_job_mock:
            job = build_raster_job(device, raster, is_text=False)

        raster_set = build_job_mock.call_args.kwargs["raster_set"]
        bw = raster_set.require(PixelFormat.BW1)
        self.assertEqual(job.payload, b"E")
        self.assertEqual((bw.width, bw.height), (8, 3))
        self.assertEqual(list(bw.pixels), [1] * 8 + [0] * 16)

    def test_build_raster_job_applies_top_padding_and_horizontal_mirror(self) -> None:
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    paper_width_px=8,
                    render_width_px=8,
                    top_padding_px=1,
                    raster_height_px=3,
                    mirror_horizontal=True,
                ),
            ),
        )
        device = replace(self.device, profile=profile)
        raster = RasterSet.from_single(
            RasterBuffer(
                pixels=[1, 0, 0, 0, 0, 0, 0, 0],
                width=8,
                pixel_format=PixelFormat.BW1,
            )
        )

        with patch(
            "timiniprint.protocol.job._build_job_model_from_raster_set",
            return_value=(b"F", ()),
        ) as build_job_mock:
            job = build_raster_job(device, raster, is_text=False)

        raster_set = build_job_mock.call_args.kwargs["raster_set"]
        bw = raster_set.require(PixelFormat.BW1)
        self.assertEqual(job.payload, b"F")
        self.assertEqual((bw.width, bw.height), (8, 3))
        self.assertEqual(
            list(bw.pixels),
            [0] * 8 + [0, 0, 0, 0, 0, 0, 0, 1] + [0] * 8,
        )

    def test_build_raster_job_rejects_raster_taller_than_exact_height(self) -> None:
        profile = replace(
            self.device.profile,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    raster_height_px=1,
                ),
            ),
        )
        device = replace(self.device, profile=profile)
        raster = RasterSet.from_single(
            RasterBuffer(
                pixels=[0] * 16,
                width=8,
                pixel_format=PixelFormat.BW1,
            )
        )

        with self.assertRaisesRegex(ValueError, "exceeds paper raster height 1px"):
            build_raster_job(device, raster, is_text=False)

    def test_raster_job_import_does_not_load_rendering_layer(self) -> None:
        script = """
import sys
import timiniprint.printing.raster_job
loaded = sorted(
    name for name in sys.modules
    if name == "PIL" or name.startswith("PIL.") or name.startswith("timiniprint.rendering")
)
if loaded:
    raise SystemExit("\\n".join(loaded))
"""
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
