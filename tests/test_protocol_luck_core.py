from __future__ import annotations

import unittest
from dataclasses import replace

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import (
    ImageEncoding,
    ImagePipelineConfig,
    PaperMode,
    PrinterProtocol,
    ProtocolFamily,
    RuntimePrintCapabilities,
)
from timiniprint.protocol.families.base import PrintJobRequest
from timiniprint.protocol.families.luck.core import (
    LuckNormalModeRecipe,
    LuckNormalPageMarkerFlow,
    LuckNormalPaperMode,
    LuckNormalVariantRecipe,
)
from timiniprint.protocol.families.luck.normal import RECIPE as PUBLIC_RECIPE
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


class LuckNormalCoreRecipeTests(unittest.TestCase):
    def test_raw_and_gray_bitmap_dimensions_do_not_wrap(self) -> None:
        catalog = PrinterCatalog.load()
        for profile_key, pixel_format, encoding, command in (
            ("luck_a2", PixelFormat.BW1, ImageEncoding.LUCK_NORMAL_RAW, b"\x1d\x76\x30\x00"),
            ("luck_ppa2l", PixelFormat.GRAY4, ImageEncoding.LUCK_NORMAL_GRAY, b"\x1d\x47\x59\x10"),
        ):
            protocol = PrinterProtocol(catalog.device_from_profile(profile_key))
            for height in (0, 0xFFFF, 0x10000):
                with self.subTest(profile=profile_key, height=height):
                    raster = RasterSet.from_single(
                        RasterBuffer(bytes(height), 1, pixel_format)
                    )
                    if height == 0 or height > 0xFFFF:
                        with self.assertRaisesRegex(ValueError, "raster dimensions"):
                            protocol.build_job(raster, is_text=False, image_encoding_override=encoding)
                    else:
                        job = protocol.build_job(
                            raster, is_text=False, image_encoding_override=encoding,
                        )
                        bitmap = next(step.data for step in job.steps if step.label == "bitmap")
                        self.assertEqual(bitmap[:4], command)
                        self.assertEqual(bitmap[4:8], b"\x01\x00\xff\xff")

    def test_behavior_binds_variant_capabilities_and_operations_to_the_same_recipe(self) -> None:
        recipe = replace(PUBLIC_RECIPE, variants={
            "bound": LuckNormalVariantRecipe(
                mode_recipes={PaperMode.TAG: LuckNormalModeRecipe()},
                image_encoding_support={ImageEncoding.LUCK_NORMAL_GRAY: (PixelFormat.GRAY4,)},
                end_line_dots_200dpi=17,
                gray_level_override=4,
                dialect=replace(PUBLIC_RECIPE.dialect, reverse_feed_command=b"\x1f\x11\x10"),
            ),
        })
        behavior = recipe.build_behavior()
        self.assertEqual(behavior.supported_protocol_variants, ("bound",))
        self.assertEqual(behavior.supported_paper_modes_for("bound"), (PaperMode.TAG,))
        self.assertEqual(behavior.image_encoding_support_for("bound"),
                         {ImageEncoding.LUCK_NORMAL_GRAY: (PixelFormat.GRAY4,)})
        self.assertEqual(behavior.advance_paper_builder(203, recipe.protocol_family, "bound"), b"\x1b\x4a\x11")
        self.assertEqual(behavior.retract_paper_builder(203, recipe.protocol_family, "bound"), b"\x1f\x11\x10\x11")
        request = self._request("bound", gray=True)
        self.assertEqual(behavior.job_builder(request), recipe.build_job(request))
        bitmap = next(step.data for step in behavior.job_builder(request).steps if step.label == "bitmap")
        self.assertEqual(bitmap, bytes.fromhex("1d 47 59 04 01 00 01 00 30"))

    @staticmethod
    def _request(
        protocol_variant: str,
        *,
        page_index: int = 1,
        page_count: int = 1,
        gray: bool = False,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
    ) -> PrintJobRequest:
        if gray:
            raster = RasterBuffer(
                pixels=[15, 0],
                width=2,
                pixel_format=PixelFormat.GRAY4,
            )
            pipeline = ImagePipelineConfig(
                formats=(PixelFormat.GRAY4,),
                encoding=ImageEncoding.LUCK_NORMAL_GRAY,
            )
        else:
            raster = RasterBuffer(
                pixels=[1] * 8,
                width=8,
                pixel_format=PixelFormat.BW1,
            )
            pipeline = ImagePipelineConfig(
                formats=(PixelFormat.BW1,),
                encoding=ImageEncoding.LUCK_NORMAL_RAW,
            )
        return PrintJobRequest(
            raster_set=RasterSet.from_single(raster),
            image_pipeline=pipeline,
            is_text=False,
            speed=None,
            energy=5000,
            blackening=3,
            lsb_first=True,
            protocol_family=ProtocolFamily.LUCK_NORMAL,
            protocol_variant=protocol_variant,
            feed_padding=0,
            dev_dpi=203,
            paper_mode=PaperMode.TAG,
            page_index=page_index,
            page_count=page_count,
            runtime_capabilities=runtime_capabilities,
        )

    def test_first_middle_last_marker_flow_wraps_page_bitmaps(self) -> None:
        variant = "marker_test"
        recipe = replace(
            PUBLIC_RECIPE,
            variants={
                variant: LuckNormalVariantRecipe(
                    mode_recipes={
                        PaperMode.TAG: LuckNormalModeRecipe(
                            paper_mode=LuckNormalPaperMode.TAG,
                            finish_action="none",
                            page_marker_flow=LuckNormalPageMarkerFlow.FIRST_MIDDLE_LAST,
                        )
                    },
                )
            },
        )

        first = recipe.build_steps(
            self._request(variant, page_index=1, page_count=2)
        )
        last = recipe.build_steps(
            self._request(variant, page_index=2, page_count=2)
        )

        self.assertEqual(
            [step.label for step in first],
            [
                "status",
                "enable",
                "wakeup",
                "paper type",
                "mark first",
                "bitmap",
                "mark not last",
                "finalize",
            ],
        )
        self.assertEqual(first[4].data, bytes.fromhex("1b bb cc"))
        self.assertEqual(first[-2].data, bytes.fromhex("1b bb aa"))
        self.assertEqual(
            [step.label for step in last],
            ["status", "enable", "wakeup", "paper type", "bitmap", "mark last", "finalize"],
        )
        self.assertEqual(last[-2].data, bytes.fromhex("1b bb bb"))

    def test_variant_gray_level_is_used_without_runtime_probe(self) -> None:
        variant = "fixed_gray_test"
        recipe = replace(
            PUBLIC_RECIPE,
            variants={
                variant: LuckNormalVariantRecipe(
                    mode_recipes={PaperMode.TAG: LuckNormalModeRecipe()},
                    gray_level_override=4,
                )
            },
        )

        steps = recipe.build_steps(self._request(variant, gray=True))
        bitmap = next(step for step in steps if step.label == "bitmap")

        self.assertEqual(bitmap.data[:8], bytes.fromhex("1d 47 59 04 01 00 01 00"))
        self.assertEqual(bitmap.data[8:], bytes([0x30]))

    def test_runtime_gray_level_takes_precedence_over_variant_default(self) -> None:
        variant = "fixed_gray_test"
        recipe = replace(
            PUBLIC_RECIPE,
            variants={
                variant: LuckNormalVariantRecipe(
                    mode_recipes={PaperMode.TAG: LuckNormalModeRecipe()},
                    gray_level_override=4,
                )
            },
        )

        steps = recipe.build_steps(
            self._request(
                variant,
                gray=True,
                runtime_capabilities=RuntimePrintCapabilities(
                    supports_gray=True,
                    gray_level_override=12,
                ),
            )
        )
        bitmap = next(step for step in steps if step.label == "bitmap")

        self.assertEqual(bitmap.data[3], 12)


if __name__ == "__main__":
    unittest.main()
