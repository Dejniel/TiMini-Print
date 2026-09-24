from __future__ import annotations

from ....raster import PixelFormat
from ...family import ProtocolFamily
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from .core import (
    LUCK_NORMAL_IMAGE_SUPPORT,
    LUCK_NORMAL_MONO_IMAGE_SUPPORT,
    LUCK_NORMAL_MODE2_DIALECT,
    LuckNormalFamilyRecipe,
    LuckNormalModeRecipe,
    LuckNormalPageMarkerFlow,
    LuckNormalPaperMode,
    LuckNormalVariantRecipe,
)

TAG_POSITION_RECIPE = LuckNormalModeRecipe(
    paper_mode=LuckNormalPaperMode.TAG,
    finish_action="position",
    wait_for_paper_reply=False,
)

BLACK_TAG_POSITION_RECIPE = LuckNormalModeRecipe(finish_action="position")
TATTOO_RECIPE = LuckNormalModeRecipe(paper_mode=LuckNormalPaperMode.TATTOO)

LUJIANG_NORMAL_MODE_RECIPES = {
    PaperMode.PLAIN: LuckNormalModeRecipe(
        page_marker_flow=LuckNormalPageMarkerFlow.LAST_ONLY,
    ),
    PaperMode.TAG: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.TAG,
        finish_action="position",
        page_marker_flow=LuckNormalPageMarkerFlow.LAST_ONLY,
    ),
    PaperMode.BLACK_TAG: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.BLACK_TAG,
        finish_action="position",
        page_marker_flow=LuckNormalPageMarkerFlow.LAST_ONLY,
    ),
    PaperMode.TATTOO: TATTOO_RECIPE,
}

QIRUI_MODE_RECIPES = {
    PaperMode.PLAIN: LuckNormalModeRecipe(),
    PaperMode.TAG: LuckNormalModeRecipe(finish_action="position"),
    PaperMode.BLACK_TAG: BLACK_TAG_POSITION_RECIPE,
    PaperMode.TATTOO: TATTOO_RECIPE,
}

RECIPE = LuckNormalFamilyRecipe(
    protocol_family=ProtocolFamily.LUCK_NORMAL,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.LUCK_NORMAL_RAW,
    ),
    image_encoding_support=LUCK_NORMAL_MONO_IMAGE_SUPPORT,
    mode_recipes={
        PaperMode.PLAIN: LuckNormalModeRecipe(),
        PaperMode.TAG: TAG_POSITION_RECIPE,
        PaperMode.BLACK_TAG: BLACK_TAG_POSITION_RECIPE,
        PaperMode.TATTOO: TATTOO_RECIPE,
    },
    end_line_dots_200dpi=80,
    end_line_dots_300dpi=120,
    variants={
        "lujiang_normal": LuckNormalVariantRecipe(
            mode_recipes=LUJIANG_NORMAL_MODE_RECIPES,
            image_encoding_support=LUCK_NORMAL_IMAGE_SUPPORT,
        ),
        "lujiang_normal_h": LuckNormalVariantRecipe(
            mode_recipes=LUJIANG_NORMAL_MODE_RECIPES,
            image_encoding_support=LUCK_NORMAL_IMAGE_SUPPORT,
        ),
        "qirui_q1": LuckNormalVariantRecipe(
            dialect=LUCK_NORMAL_MODE2_DIALECT,
            mode_recipes=QIRUI_MODE_RECIPES,
        ),
        "qirui_q2": LuckNormalVariantRecipe(
            dialect=LUCK_NORMAL_MODE2_DIALECT,
            mode_recipes=QIRUI_MODE_RECIPES,
            end_line_dots_300dpi=130,
        ),
    },
)


BEHAVIOR = RECIPE.build_behavior()
