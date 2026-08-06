from __future__ import annotations

import unittest
from dataclasses import replace

from timiniprint.protocol import (
    ImageEncoding,
    ImagePipelineConfig,
    PaperMode,
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
                "enable",
                "wakeup",
                "paper type",
                "mark first",
                "bitmap",
                "mark not last",
                "finalize",
            ],
        )
        self.assertEqual(first[3].data, bytes.fromhex("1b bb cc"))
        self.assertEqual(first[-2].data, bytes.fromhex("1b bb aa"))
        self.assertEqual(
            [step.label for step in last],
            ["enable", "wakeup", "paper type", "bitmap", "mark last", "finalize"],
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
