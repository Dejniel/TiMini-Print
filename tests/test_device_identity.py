from __future__ import annotations

from dataclasses import replace

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import ModelDetection, SupportedPrinterModel


def catalog(*, ambiguous=False):
    profile = PrinterCatalog.load().require_profile("x6h")
    models = [
        SupportedPrinterModel(
            model_key="first", profile_key="x6h", origin_ids=("source.a",),
            detections=(ModelDetection(exact_names=("BT-one",)),),
            identity_names=("internal-one", "wire-alias"),
        ),
        SupportedPrinterModel(
            model_key="second", profile_key="x6h", origin_ids=("source.a" if ambiguous else "source.b",),
            detections=(ModelDetection(exact_names=("BT-two",)),),
            identity_names=("wire-alias",),
        ),
    ]
    return PrinterCatalog([profile], models, origin_names={"source.a": "A", "source.b": "B"})


def test_identity_aliases_are_exact_case_insensitive_and_source_scoped():
    entries = catalog()
    assert entries.device_from_identity("WIRE-ALIAS", origin_id="source.a").model_key == "first"
    assert entries.device_from_identity("wire-alias", origin_id="source.b").model_key == "second"
    assert entries.device_from_identity("internal-one", origin_id="source.a").model_key == "first"
    assert entries.detect_device("wire-alias") is None
    assert entries.detect_device("BT-one").model_key == "first"


@pytest.mark.parametrize("identity", ["BT-one", "wire", "wire-alias-123", " wire-alias", "wire alias"])
def test_identity_lookup_does_not_guess_from_scan_rules_or_whitespace(identity):
    with pytest.raises(ValueError, match="Unknown printer identity"):
        catalog().device_from_identity(identity, origin_id="source.a")


def test_identity_collision_is_not_resolved_by_catalog_order():
    with pytest.raises(ValueError, match="Ambiguous printer identity"):
        catalog(ambiguous=True).device_from_identity("wire-alias", origin_id="source.a")


def test_profile_refinement_preserves_effective_protocol_and_pipeline():
    device = PrinterCatalog.load().device_from_profile("niimbot_d110")
    selected = device.with_protocol_variant("b1")
    geometry = replace(device.profile, dev_dpi=300)
    refined = selected.with_print_profile(geometry)
    assert refined.profile.dev_dpi == 300
    assert refined.protocol_variant == refined.profile.protocol_default.packets_type == "b1"
    assert refined.image_pipeline == refined.profile.default_image_pipeline == device.image_pipeline
    assert device.protocol_variant != "b1"
