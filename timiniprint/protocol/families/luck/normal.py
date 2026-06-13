from __future__ import annotations

from ....raster import PixelFormat
from ...family import ProtocolFamily
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from ..base import ProtocolBehavior
from .core import (
    LUCK_NORMAL_IMAGE_SUPPORT,
    LUCK_NORMAL_MODE2_DIALECT,
    LuckNormalFamilyRecipe,
    LuckNormalModeRecipe,
    LuckNormalPaperMode,
    LuckNormalVariantRecipe,
)

TAG_POSITION_RECIPE = LuckNormalModeRecipe(
    paper_mode=LuckNormalPaperMode.TAG,
    finish_action="position",
)

LUJIANG_NORMAL_MODE_RECIPES = {
    PaperMode.PLAIN: LuckNormalModeRecipe(
        mark_last_scope="last_page",
    ),
    PaperMode.TAG: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.TAG,
        finish_action="position",
        mark_last_scope="last_page",
    ),
}

QIRUI_MODE_RECIPES = {
    PaperMode.PLAIN: LuckNormalModeRecipe(),
    PaperMode.TAG: TAG_POSITION_RECIPE,
}

RECIPE = LuckNormalFamilyRecipe(
    protocol_family=ProtocolFamily.LUCK_NORMAL,
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1, PixelFormat.GRAY4, PixelFormat.GRAY8),
        encoding=ImageEncoding.LUCK_NORMAL_RAW,
    ),
    image_encoding_support=LUCK_NORMAL_IMAGE_SUPPORT,
    mode_recipes={
        PaperMode.PLAIN: LuckNormalModeRecipe(),
        PaperMode.TAG: TAG_POSITION_RECIPE,
    },
    end_line_dots_200dpi=80,
    end_line_dots_300dpi=120,
    variants={
        "lujiang_normal": LuckNormalVariantRecipe(
            mode_recipes=LUJIANG_NORMAL_MODE_RECIPES,
            query_interleaved=True,
        ),
        "lujiang_normal_h": LuckNormalVariantRecipe(
            mode_recipes=LUJIANG_NORMAL_MODE_RECIPES,
            end_line_dots_300dpi=60,
            query_interleaved=True,
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


BEHAVIOR = ProtocolBehavior(
    default_image_pipeline=RECIPE.default_image_pipeline,
    image_encoding_support=RECIPE.image_encoding_support,
    supported_protocol_variants=RECIPE.supported_variants(),
    supported_paper_modes=RECIPE.supported_paper_modes(),
    supported_paper_modes_resolver=RECIPE.supported_paper_modes,
    advance_paper_builder=RECIPE.build_advance_paper,
    retract_paper_builder=RECIPE.build_retract_paper,
    job_builder=RECIPE.build_job,
)
