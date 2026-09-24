from dataclasses import replace

import pytest

from timiniprint.protocol import ImageEncoding, ImagePipelineConfig, PaperMode, ProtocolFamily, ProtocolStepOperation
from timiniprint.protocol.families.base import PageFlow, PrintJobRequest
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet
from timiniprint.protocol.families.luck.core import (
    LUCK_NORMAL_DIALECT, LuckNormalModeRecipe, LuckNormalPageMarkerFlow,
    LuckNormalVariantRecipe,
)
from timiniprint.protocol.families.luck.normal import RECIPE


@pytest.mark.parametrize("index", [1, 2, 3])
@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("flow", [PageFlow.PAGED, PageFlow.CONTINUOUS])
def test_media_tail_options_preserve_explicit_step_order(index, wait, flow):
    mode = LuckNormalModeRecipe(
        finish_action="position", set_raster_width=True, last_page_feed_dots=40,
        page_marker_flow=LuckNormalPageMarkerFlow.LAST_ONLY,
        adjust_after=0x50, adjust_after_scope="last_page", wait_for_finalize_reply=wait,
    )
    recipe = replace(RECIPE, variants={"test": LuckNormalVariantRecipe(mode_recipes={PaperMode.TAG: mode})})
    request = PrintJobRequest(
        raster_set=RasterSet.from_single(RasterBuffer([1] * 8, 8, PixelFormat.BW1)),
        image_pipeline=ImagePipelineConfig(formats=(PixelFormat.BW1,), encoding=ImageEncoding.LUCK_NORMAL_RAW),
        is_text=False, speed=None, energy=5000, blackening=3, lsb_first=False,
        protocol_family=ProtocolFamily.LUCK_NORMAL, protocol_variant="test", feed_padding=0,
        dev_dpi=203, paper_mode=PaperMode.TAG, page_index=index, page_count=3, page_flow=flow,
    )
    steps = recipe.build_steps(request)
    assert [s.label for s in steps] == [
        "status", "enable", "wakeup", "raster width", "bitmap",
        *(["position"] if request.ends_media_page else []),
        *(["last page feed", "mark last", "adjust after"] if index == 3 else []), "finalize",
    ]
    assert steps[3].data == bytes.fromhex("10 ff 15 08 00")
    assert steps[-1].operation is (ProtocolStepOperation.QUERY if wait else ProtocolStepOperation.SEND)
    assert steps[-1].reply_required == wait
    if index == 3:
        assert [s.data for s in steps[-4:-1]] == [
            bytes.fromhex("1b 4a 28"), bytes.fromhex("1b bb bb"), bytes.fromhex("1f 11 50"),
        ]
    plan = recipe.build_job(request)
    assert plan.payload == b"".join(s.data for s in plan.steps if s.include_in_payload)


def test_reverse_feed_dialect_does_not_change_forward_feed():
    dialect = replace(LUCK_NORMAL_DIALECT, reverse_feed_command=b"\x1f\x11\x10")
    assert dialect.reverse_feed(140) == bytes.fromhex("1f 11 10 8c")
    assert dialect.line_feed(140) == LUCK_NORMAL_DIALECT.line_feed(140)
    assert LUCK_NORMAL_DIALECT.reverse_feed(140) == bytes.fromhex("1f 11 11 8c")
