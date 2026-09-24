from __future__ import annotations

from ....raster import PixelFormat
from ...family import ProtocolFamily
from ...types import ImageEncoding, ImagePipelineConfig, PaperMode
from .transactions import LUJIANG_A4_FINALIZE_TIMEOUT_SEC
from .core import (
    LUCK_NORMAL_MONO_IMAGE_SUPPORT,
    LuckNormalFamilyRecipe,
    LuckNormalModeRecipe,
    LuckNormalPaperMode,
    LuckNormalVariantRecipe,
)

A4_MARKED_TAG_RECIPE = LuckNormalModeRecipe(
    paper_mode=LuckNormalPaperMode.TAG,
    finish_action="position",
    adjust_before=81,
    adjust_before_scope="first_page",
    adjust_after=80,
    adjust_after_scope="last_page",
)

A4_MARKED_BLACK_TAG_RECIPE = LuckNormalModeRecipe(
    paper_mode=LuckNormalPaperMode.BLACK_TAG,
    finish_action="position",
    adjust_before=81,
    adjust_before_scope="first_page",
    adjust_after=80,
    adjust_after_scope="last_page",
)

A4_MARKED_FOLDER_RECIPE = LuckNormalModeRecipe(
    paper_mode=LuckNormalPaperMode.FOLDER,
    finish_action="position",
    adjust_before=81,
    adjust_before_scope="always",
    adjust_after=80,
    adjust_after_scope="always",
)

A4_TATTOO_64_MODE_RECIPES = {
    PaperMode.PLAIN: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.PLAIN,
    ),
    PaperMode.TAG: A4_MARKED_TAG_RECIPE,
    PaperMode.BLACK_TAG: A4_MARKED_BLACK_TAG_RECIPE,
    PaperMode.FOLDER: A4_MARKED_FOLDER_RECIPE,
    PaperMode.TATTOO: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.TATTOO,
    ),
}

BASE_A4_MODE_RECIPES = {
    PaperMode.PLAIN: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.PLAIN,
    ),
    PaperMode.TAG: A4_MARKED_TAG_RECIPE,
    PaperMode.BLACK_TAG: A4_MARKED_BLACK_TAG_RECIPE,
    PaperMode.FOLDER: A4_MARKED_FOLDER_RECIPE,
    PaperMode.TATTOO: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.PLAIN,
    ),
}

LUCKP_A4_MODE_RECIPES = {
    PaperMode.PLAIN: LuckNormalModeRecipe(),
    PaperMode.TAG: A4_MARKED_TAG_RECIPE,
    PaperMode.BLACK_TAG: A4_MARKED_BLACK_TAG_RECIPE,
    PaperMode.FOLDER: LuckNormalModeRecipe(
        finish_action="position",
    ),
    PaperMode.TATTOO: LuckNormalModeRecipe(),
}

# BaseA4Device branch with paper type sent before enable/wakeup.
APL86_MODE_RECIPES = {
    PaperMode.PLAIN: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.PLAIN,
        paper_type_stage="before_enable",
    ),
    PaperMode.TAG: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.TAG,
        paper_type_stage="before_enable",
        finish_action="position",
        adjust_before=81,
        adjust_before_scope="first_page",
        adjust_after=80,
        adjust_after_scope="last_page",
    ),
    PaperMode.BLACK_TAG: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.BLACK_TAG,
        paper_type_stage="before_enable",
        finish_action="position",
        adjust_before=81,
        adjust_before_scope="first_page",
        adjust_after=80,
        adjust_after_scope="last_page",
    ),
    PaperMode.FOLDER: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.FOLDER,
        paper_type_stage="before_enable",
        finish_action="position",
        adjust_before=81,
        adjust_before_scope="always",
        adjust_after=80,
        adjust_after_scope="always",
    ),
    PaperMode.TATTOO: LuckNormalModeRecipe(
        paper_mode=LuckNormalPaperMode.TATTOO,
        paper_type_stage="before_enable",
    ),
}

RECIPE = LuckNormalFamilyRecipe(
    protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
    # Source app often defaults A4 models to compressed transfer. The repo keeps
    # raw as the family default until each mapped model explicitly opts into the
    # compressed path.
    default_image_pipeline=ImagePipelineConfig(
        formats=(PixelFormat.BW1,),
        encoding=ImageEncoding.LUCK_NORMAL_RAW,
    ),
    image_encoding_support=LUCK_NORMAL_MONO_IMAGE_SUPPORT,
    mode_recipes=BASE_A4_MODE_RECIPES,
    end_line_dots_200dpi=144,
    end_line_dots_300dpi=216,
    variants={
        # LuckP keeps the A4 transport but omits setPaperType(...) in several
        # plain-media flows.
        "luckp_a41": LuckNormalVariantRecipe(
            mode_recipes=LUCKP_A4_MODE_RECIPES,
            speed_range=(0, 8),
        ),
        "luckp_a42": LuckNormalVariantRecipe(
            mode_recipes=LUCKP_A4_MODE_RECIPES,
        ),
        # Lujiang stays in the same family but changes marker handling and
        # shorter 200dpi line feeds.
        "lujiang_a4": LuckNormalVariantRecipe(
            mode_recipes=A4_TATTOO_64_MODE_RECIPES,
            end_line_dots_200dpi=96,
            finalize_timeout_sec=LUJIANG_A4_FINALIZE_TIMEOUT_SEC,
        ),
        # Compressed A4 branches reuse the same bitmap codec but change paper
        # recipes and line-feed defaults.
        "a4_tattoo_64": LuckNormalVariantRecipe(
            mode_recipes=A4_TATTOO_64_MODE_RECIPES,
        ),
        "a4_tattoo_64_endline96": LuckNormalVariantRecipe(
            mode_recipes=A4_TATTOO_64_MODE_RECIPES,
            end_line_dots_200dpi=96,
        ),
        "u8": LuckNormalVariantRecipe(
            mode_recipes=A4_TATTOO_64_MODE_RECIPES,
        ),
        "apl86": LuckNormalVariantRecipe(
            mode_recipes=APL86_MODE_RECIPES,
        ),
        "d80": LuckNormalVariantRecipe(
            # Without serial-range overrides, tattoo uses 0x40 and no extra heating.
            mode_recipes=A4_TATTOO_64_MODE_RECIPES,
        ),
        "d80h": LuckNormalVariantRecipe(
            mode_recipes=A4_TATTOO_64_MODE_RECIPES,
        ),
        "a49h": LuckNormalVariantRecipe(
            mode_recipes=A4_TATTOO_64_MODE_RECIPES,
            end_line_dots_300dpi=96,
        ),
        # This compressWay=1 branch currently reuses the shared base A4 recipe.
        "a80h_way1": LuckNormalVariantRecipe(),
    },
)


BEHAVIOR = RECIPE.build_behavior()
