"""The existing continuous-raster contract can depend on the selected medium."""
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from tests.test_printing_job import _FakeDocumentRenderer, _rendered_page
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import PageFlow, PrinterProtocol
from timiniprint.protocol.families import get_protocol_behavior
from timiniprint.raster import PixelFormat


@pytest.fixture
def device():
    device = PrinterCatalog.load().device_from_profile("x6h")
    paper = device.profile.default_paper_preset
    papers = tuple(replace(paper, key=key) for key in ("stream", "whole"))
    return device.with_print_profile(replace(device.profile, paper_presets=papers,
                                            default_paper_preset_key="stream"))


def test_query_resolves_default_and_selected_paper_and_rejects_unknown_keys(device):
    resolver = Mock(side_effect=lambda variant, key: key == "whole")
    behavior = replace(get_protocol_behavior(device.protocol_family), contiguous_raster_resolver=resolver)
    with patch("timiniprint.protocol.job.get_protocol_behavior", return_value=behavior):
        protocol = PrinterProtocol(device)
        assert not protocol.requires_contiguous_raster_for_continuous_pages()
        assert protocol.requires_contiguous_raster_for_continuous_pages(paper_preset_key="whole")
        assert resolver.call_args_list[0].args == (device.protocol_variant, "stream")
        assert resolver.call_args_list[1].args == (device.protocol_variant, "whole")
        with pytest.raises(ValueError, match="Unknown paper preset"):
            protocol.requires_contiguous_raster_for_continuous_pages(paper_preset_key="missing")
        assert resolver.call_count == 2


def test_variant_declaration_is_unchanged_without_a_media_resolver(device):
    behavior = replace(get_protocol_behavior(device.protocol_family),
                       contiguous_raster_variants=(device.protocol_variant,))
    with patch("timiniprint.protocol.job.get_protocol_behavior", return_value=behavior):
        assert PrinterProtocol(device).requires_contiguous_raster_for_continuous_pages()
        assert PrinterProtocol(device).requires_contiguous_raster_for_continuous_pages(paper_preset_key="whole")


@pytest.mark.parametrize("paper", ["stream", "whole"])
@pytest.mark.parametrize("flow", [PageFlow.CONTINUOUS, PageFlow.PAGED])
@pytest.mark.parametrize("streaming", [False, True])
def test_builder_obeys_selected_media_without_merging_independent_pages(device, paper, flow, streaming):
    pipeline = PrinterProtocol(device).resolve_image_pipeline()
    renderer = _FakeDocumentRenderer([_rendered_page(pipeline=pipeline)] * 2)
    plan_document = renderer.plan_document
    behavior = replace(get_protocol_behavior(device.protocol_family),
                       contiguous_raster_resolver=lambda variant, key: key == "whole")
    builder = PrintJobBuilder(device, PrintSettings(paper_preset_key=paper), document_renderer=renderer)
    with patch("timiniprint.protocol.job.get_protocol_behavior", return_value=behavior), patch.object(
        renderer, "plan_document", side_effect=lambda *args: replace(plan_document(*args), page_flow=flow),
    ), patch("timiniprint.printing.builder.os.path.isfile", return_value=True), patch(
        "timiniprint.protocol.job._build_job_model_from_raster_set", return_value=(b"page", ()),
    ) as encode:
        if streaming:
            jobs = list(builder.iter_page_jobs("text.txt"))
            assert len(jobs) == (1 if paper == "whole" and flow is PageFlow.CONTINUOUS else 2)
        else:
            builder.build_from_file("text.txt")
    rasters = [call.kwargs["raster_set"].require(PixelFormat.BW1) for call in encode.call_args_list]
    assert [raster.height for raster in rasters] == ([2] if paper == "whole" and flow is PageFlow.CONTINUOUS else [1, 1])
