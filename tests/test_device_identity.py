from __future__ import annotations

from dataclasses import replace

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.device import SerialTarget
from timiniprint.devices.profiles import ModelDetection, RuntimeCapabilities, RuntimeSettings, SupportedPrinterModel


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


@pytest.mark.parametrize("same_profile", [False, True])
@pytest.mark.parametrize("same_model", [False, True])
def test_connection_resolution_keeps_only_matching_profile_overrides(same_profile, same_model):
    entries = PrinterCatalog.load()
    original = entries.device_from_profile("x6h")
    original = replace(
        original, display_name="User's printer", transport_target=SerialTarget("/dev/test"),
        runtime_settings=RuntimeSettings(capabilities=RuntimeCapabilities(d2_status=True)),
        profile=replace(original.profile, post_print_feed_count=9,
                        stream=replace(original.profile.stream, chunk_size=17),
                        use_spp=True, ble_mtu_request=64),
    )
    identified = entries.device_from_profile("v5g_small_203")
    identified = replace(
        identified, model_key=original.model_key if same_model else "confirmed-model",
        origin_ids=("confirmed-source",),
        profile=replace(identified.profile,
                        profile_key=original.profile_key if same_profile else identified.profile_key),
    )

    identified_profile = identified.profile
    resolved = identified.resolve_for_connection(original)

    expected_profile = original.profile if same_profile else identified.profile
    assert resolved.profile.post_print_feed_count == expected_profile.post_print_feed_count
    assert resolved.profile.paper_presets == expected_profile.paper_presets
    assert resolved.profile.dev_dpi == expected_profile.dev_dpi
    assert resolved.model_key == identified.model_key
    assert resolved.origin_ids == identified.origin_ids
    assert resolved.runtime_settings == identified.runtime_settings
    assert resolved.protocol_family == resolved.profile.protocol_default.type == identified.protocol_family
    assert resolved.protocol_variant == resolved.profile.protocol_default.packets_type == identified.protocol_variant
    assert resolved.image_pipeline == resolved.profile.default_image_pipeline == identified.image_pipeline
    assert resolved.display_name == original.display_name
    assert resolved.transport_target == original.transport_target
    assert resolved.profile.stream == original.profile.stream
    assert resolved.profile.use_spp == original.profile.use_spp
    assert resolved.profile.ble_mtu_request == original.profile.ble_mtu_request
    assert identified.profile is identified_profile
    assert original.profile.post_print_feed_count == 9


@pytest.mark.parametrize("same_profile", [False, True])
def test_connection_resolution_applies_hardware_constraints_after_user_overrides(same_profile):
    entries = PrinterCatalog.load()
    original = entries.device_from_profile("niimbot_d110")
    original = original.with_print_profile(replace(original.profile, dev_dpi=111, post_print_feed_count=9))
    identified = original.with_protocol_variant("d110") if same_profile else entries.device_from_profile("x6h")
    seen = []

    def refine(profile):
        seen.append(profile)
        return replace(profile, dev_dpi=300, stream=replace(profile.stream, chunk_size=1),
                       protocol_default=original.profile.protocol_default,
                       default_image_pipeline=original.image_pipeline)

    resolved = identified.resolve_for_connection(original, refine_profile=refine)

    assert seen == [original.profile if same_profile else identified.profile]
    assert resolved.profile.dev_dpi == 300
    assert resolved.profile.post_print_feed_count == (9 if same_profile else 2)
    assert resolved.profile.stream == original.profile.stream
    assert resolved.profile.protocol_default.type == identified.protocol_family
    assert resolved.profile.protocol_default.packets_type == identified.protocol_variant
    assert resolved.profile.default_image_pipeline == identified.image_pipeline
    assert original.profile.dev_dpi == 111
