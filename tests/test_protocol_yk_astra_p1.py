from __future__ import annotations

import unittest
from dataclasses import replace

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import ImageEncoding, ImagePipelineConfig, PaperMode, PrinterProtocol
from timiniprint.protocol.families.base import PrintJobRequest
from timiniprint.protocol.families.yk_common import iter_yk_frames
from timiniprint.protocol.families.yk_astra_p1.core import PUBLIC_RECIPE, S001_VARIANT
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


def _request(
    *,
    paper_mode: PaperMode = PaperMode.TAG,
    pixels: list[int] | None = None,
    width: int = 1,
    page_index: int = 1,
    page_count: int = 1,
    density: int = 9,
) -> PrintJobRequest:
    raster = RasterBuffer(
        pixels=[1] if pixels is None else pixels,
        width=width,
        pixel_format=PixelFormat.BW1,
    )
    return PrintJobRequest(
        raster_set=RasterSet.from_single(raster),
        image_pipeline=ImagePipelineConfig(
            formats=(PixelFormat.BW1,),
            encoding=ImageEncoding.YK_ASTRA_P1_RAW,
        ),
        is_text=False,
        speed=25,
        energy=0,
        density=density,
        blackening=3,
        lsb_first=False,
        protocol_family="yk_astra_p1",
        protocol_variant="s001",
        feed_padding=0,
        dev_dpi=203,
        paper_mode=paper_mode,
        page_index=page_index,
        page_count=page_count,
    )


class AstraP1S001Tests(unittest.TestCase):
    def test_single_gap_label_uses_source_backed_command_order(self) -> None:
        job = PUBLIC_RECIPE.build_job(
            _request(pixels=[1, 0, 0, 0], width=1)
        )
        frames = list(iter_yk_frames(job.payload))

        self.assertEqual(
            [frame.command for frame in frames],
            [0x0A, 0x09, 0x28, 0x03, 0x02, 0x00, 0x03],
        )
        self.assertEqual([frame.sequence for frame in frames], list(range(7)))
        self.assertEqual(frames[0].payload, b"\x19")
        self.assertEqual(frames[1].payload, b"\x09")
        self.assertEqual(frames[2].payload, b"\x01\x01")
        self.assertEqual(frames[3].payload, bytes.fromhex("02 20 03"))
        self.assertEqual(frames[4].payload, bytes.fromhex("02 00"))
        self.assertEqual(frames[5].payload, b"\x02" + b"\x00" * 47)
        self.assertEqual(frames[6].payload, bytes.fromhex("01 20 03"))

    def test_middle_and_last_labels_change_only_terminal_feed_mode(self) -> None:
        first = list(
            iter_yk_frames(
                PUBLIC_RECIPE.build_job(
                    _request(page_index=1, page_count=2)
                ).payload
            )
        )
        last = list(
            iter_yk_frames(
                PUBLIC_RECIPE.build_job(
                    _request(page_index=2, page_count=2)
                ).payload
            )
        )

        self.assertEqual(first[-1].payload, bytes.fromhex("00 20 03"))
        self.assertEqual([frame.command for frame in last], [0x02, 0x00, 0x03])
        self.assertEqual(last[-1].payload, bytes.fromhex("01 20 03"))

    def test_plain_label_uses_backward_and_forward_feed_recipe(self) -> None:
        frames = list(
            iter_yk_frames(
                PUBLIC_RECIPE.build_job(
                    _request(paper_mode=PaperMode.PLAIN)
                ).payload
            )
        )

        self.assertEqual(
            [(frame.command, frame.payload) for frame in frames if frame.command != 0x00],
            [
                (0x0A, b"\x19"),
                (0x09, b"\x09"),
                (0x28, b"\x01\x00"),
                (0x04, bytes.fromhex("0c 00")),
                (0x02, bytes.fromhex("34 00")),
                (0x02, bytes.fromhex("0c 00")),
            ],
        )

    def test_raster_width_is_checked_against_fixed_head(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds 96px head"):
            PUBLIC_RECIPE.build_job(
                _request(pixels=[0] * 91, width=91)
            )

    def test_stepwise_variant_preserves_frame_boundaries(self) -> None:
        recipe = replace(
            PUBLIC_RECIPE,
            variants={"s001": replace(S001_VARIANT, stepwise=True)},
        )

        job = recipe.build_job(_request())

        self.assertTrue(job.steps)
        self.assertEqual(job.payload, b"".join(step.data for step in job.steps))
        self.assertTrue(
            all(len(tuple(iter_yk_frames(step.data))) == 1 for step in job.steps)
        )

    def test_catalog_exposes_exact_s001_profile_and_density(self) -> None:
        catalog = PrinterCatalog.load()
        device = catalog.detect_device("S001")

        self.assertIsNotNone(device)
        self.assertEqual(device.model_key, "orgstra_s001")
        self.assertEqual(device.profile_key, "yk_astra_p1_s001")
        self.assertEqual(device.protocol_family.value, "yk_astra_p1")
        self.assertEqual(device.protocol_variant, "s001")
        self.assertTrue(device.profile.use_spp)
        self.assertEqual(device.profile.default_paper_preset.rotation_degrees, 270)
        self.assertFalse(device.profile.default_paper_preset.mirror_horizontal)
        self.assertIsNone(catalog.detect_device("S001-extra"))

        raster = RasterBuffer(
            pixels=[0] * 84,
            width=84,
            pixel_format=PixelFormat.BW1,
        )
        job = PrinterProtocol(device).build_job(
            RasterSet.from_single(raster),
            is_text=False,
            blackening=5,
            paper_mode=PaperMode.TAG,
        )
        frames = list(iter_yk_frames(job.payload))
        density = next(frame.payload for frame in frames if frame.command == 0x09)
        self.assertEqual(density, b"\x0d")


if __name__ == "__main__":
    unittest.main()
