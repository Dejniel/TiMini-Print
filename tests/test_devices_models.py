from __future__ import annotations

import unittest

from tests.helpers import reset_registry_cache
from timiniprint.devices import PrinterCatalog, SupportedModelMatch, UnsupportedModelMatch
from timiniprint.devices.model_codec import model_from_json, model_to_json
from timiniprint.devices.profiles import PrinterProfile, SupportedPrinterModel
from timiniprint.protocol import PrinterProtocol
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding, PaperMode


def _profile_payload(profile_key: str = "demo", *, speed: dict | None = None) -> dict:
    if speed is None:
        speed = {"image": 10, "text": 8}
    return {
        "profile_key": profile_key,
        "size": 1,
        "paper_size": 1,
        "print_size": 384,
        "one_length": 8,
        "dev_dpi": 203,
        "has_id": False,
        "use_spp": False,
        "can_print_label": False,
        "label_value": "",
        "back_paper_num": 0,
        "protocol_default": {
            "type": "tiny",
        },
        "default_image_pipeline": {
            "formats": ["bw1"],
            "encoding": "tiny_raw",
        },
        "stream": {
            "chunk_size": 180,
            "delay_ms": 4,
        },
        "post_print_feed_count": 2,
        "print_defaults": {
            "speed": speed,
            "energy": {
                "image": {"low": 5000, "middle": 5000, "high": 5000},
                "text": {"low": 8000, "middle": 8000, "high": 8000},
            },
        },
    }


def _with_optional_speed(payload: dict, speed: dict | None) -> dict:
    payload = dict(payload)
    payload["print_defaults"] = dict(payload["print_defaults"])
    if speed is None:
        payload["print_defaults"].pop("speed", None)
    else:
        payload["print_defaults"]["speed"] = speed
    return payload


def _runtime_preset_payload(
    *,
    key: str = "demo-runtime",
    image_middle: int = 120,
) -> dict:
    return {
        "key": key,
        "control_algorithm": "mx06",
        "density": {
            "image": {"low": 100, "middle": image_middle, "high": 140},
            "text": {"low": 80, "middle": 100, "high": 120},
        },
        "capabilities": {
            "d2_status": True,
            "didian_status": False,
        },
    }


def _model_payload(
    *,
    model_key: str = "demo-model",
    profile_key: str = "demo",
    protocol_family: str | None = None,
    prefixes: list[str] | None = None,
    mac_suffixes: list[str] | None = None,
    profile_runtime_preset_key: str | None = None,
) -> dict:
    payload = {
        "model_key": model_key,
        "origin_app_packages": ["com.example.demo"],
        "detections": [
            {
                "name": "DEMO",
                "detection": {
                    "prefixes": prefixes or ["DEMO"],
                },
            }
        ],
        "profile_key": profile_key,
    }
    if protocol_family is not None:
        payload["protocol_override"] = {"type": protocol_family}
    if mac_suffixes:
        payload["detections"][0]["detection"]["mac_suffixes"] = mac_suffixes
    if profile_runtime_preset_key is not None:
        payload["profile_runtime_preset_key"] = profile_runtime_preset_key
    return payload


class DevicesModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()
        self.catalog = PrinterCatalog.load()

    def assert_runtime_settings(
        self,
        device,
        *,
        variant: str | None,
        preset_key: str | None,
        d2_status: bool = False,
        didian_status: bool = False,
    ) -> None:
        self.assertIsNotNone(device.runtime_settings)
        self.assertEqual(device.runtime_settings.control_algorithm, variant)
        self.assertEqual(device.runtime_settings.preset_key, preset_key)
        self.assertEqual(device.runtime_settings.capabilities.d2_status, d2_status)
        self.assertEqual(device.runtime_settings.capabilities.didian_status, didian_status)

    def test_catalog_loads_profiles_and_models(self) -> None:
        self.assertGreater(len(self.catalog.profiles), 0)
        self.assertGreater(len(self.catalog.models), 0)
        self.assertGreater(len(self.catalog.unsupported_models), 0)
        profile = self.catalog.require_profile("x6h")
        self.assertEqual(profile.stream.chunk_size, 180)
        self.assertEqual(profile.stream.delay_ms, 4)

    def test_unsupported_models_are_detected_without_creating_devices(self) -> None:
        self.assertIsNone(self.catalog.detect_device("P12"))

        match = self.catalog.detect_model("P12")
        unsupported = self.catalog.detect_unsupported_model("P12")

        self.assertIsInstance(match, UnsupportedModelMatch)
        self.assertIsNotNone(unsupported)
        assert unsupported is not None
        self.assertEqual(unsupported.model_key, "unsupported_phomemo_p12_family_p12")
        self.assertEqual(unsupported.names, ("P12",))

    def test_unsupported_models_keep_source_origins_and_advisory_prefixes(self) -> None:
        supported_names = {
            name
            for model in self.catalog.models
            for name in model.names
        }
        for model in self.catalog.unsupported_models:
            with self.subTest(model=model.model_key):
                self.assertTrue(model.origin_app_packages)
                self.assertNotIn(model.model_group, supported_names)
                for detection in model.detections:
                    self.assertTrue(detection.detection.prefixes)
                    self.assertFalse(detection.detection.exact_names)

    def test_unsupported_case_variants_can_stay_source_distinct(self) -> None:
        niimbot_d11s = self.catalog.detect_model("D11S")
        luck_d11s = self.catalog.detect_model("D11s_1234")

        self.assertIsInstance(niimbot_d11s, UnsupportedModelMatch)
        self.assertIsInstance(luck_d11s, UnsupportedModelMatch)
        assert isinstance(niimbot_d11s, UnsupportedModelMatch)
        assert isinstance(luck_d11s, UnsupportedModelMatch)
        self.assertEqual(niimbot_d11s.model.model_key, "unsupported_todo_niimbot_candidates_d11s")
        self.assertEqual(niimbot_d11s.model.origin_app_packages, ("com.gengcon.android.jccloudprinter",))
        self.assertEqual(luck_d11s.model.model_key, "unsupported_todo_luck_mpl11")
        self.assertEqual(luck_d11s.model.origin_app_packages, ("com.dingdang.newprint",))

    def test_detect_model_returns_supported_match_for_printable_models(self) -> None:
        match = self.catalog.detect_model("X6H-1234")

        self.assertIsInstance(match, SupportedModelMatch)
        assert isinstance(match, SupportedModelMatch)
        self.assertEqual(match.detection.name, "X6H")
        self.assertEqual(match.profile.profile_key, "x6h")

    def test_supported_model_keys_do_not_use_synthetic_model_prefix(self) -> None:
        for model in self.catalog.models:
            with self.subTest(model=model.model_key):
                self.assertFalse(model.model_key.startswith("model_"))

    def test_model_codec_rejects_non_positive_stream_chunk_size(self) -> None:
        payload = _profile_payload()
        payload["stream"]["chunk_size"] = 0
        with self.assertRaisesRegex(ValueError, "stream.chunk_size"):
            model_from_json(PrinterProfile, payload)

    def test_model_codec_allows_missing_speed_for_non_speed_family(self) -> None:
        payload = _with_optional_speed(_profile_payload(), None)
        payload["protocol_default"]["type"] = "luck_normal"

        profile = model_from_json(PrinterProfile, payload)

        self.assertIsNone(profile.speed)

    def test_model_codec_accepts_default_paper_mode(self) -> None:
        payload = _with_optional_speed(_profile_payload(), None)
        payload["protocol_default"]["type"] = "luck_normal"
        payload["default_paper_mode"] = "tag"

        profile = model_from_json(PrinterProfile, payload)

        self.assertEqual(profile.default_paper_mode, PaperMode.TAG)

    def test_model_codec_rejects_unknown_catalog_fields(self) -> None:
        payload = _profile_payload()
        payload["extra"] = "bad"

        with self.assertRaisesRegex(ValueError, "unknown PrinterProfile field"):
            model_from_json(PrinterProfile, payload)

    def test_model_codec_profile_roundtrip_keeps_normalized_shape(self) -> None:
        payload = _profile_payload()
        profile = model_from_json(PrinterProfile, payload)

        payload = model_to_json(profile)

        self.assertEqual(payload["profile_key"], "demo")
        self.assertEqual(payload["protocol_default"]["packets_type"], None)
        self.assertEqual(payload["default_paper_mode"], None)
        self.assertEqual(payload["ble_mtu_request"], 512)
        self.assertEqual(payload["print_defaults"]["speed"]["image"], 10)

    def test_profile_ble_mtu_request_defaults_to_512(self) -> None:
        profile = model_from_json(PrinterProfile, _profile_payload())
        self.assertEqual(profile.ble_mtu_request, 512)

    def test_profile_ble_mtu_request_allows_standard_ble_mtu(self) -> None:
        payload = _profile_payload()
        payload["ble_mtu_request"] = 23
        profile = model_from_json(PrinterProfile, payload)
        self.assertEqual(profile.ble_mtu_request, 23)

    def test_model_codec_rejects_null_ble_mtu_request(self) -> None:
        payload = _profile_payload()
        payload["ble_mtu_request"] = None

        with self.assertRaisesRegex(ValueError, "ble_mtu_request"):
            model_from_json(PrinterProfile, payload)

    def test_model_codec_rejects_invalid_ble_mtu_request(self) -> None:
        payload = _profile_payload()
        payload["ble_mtu_request"] = 22

        with self.assertRaisesRegex(ValueError, "ble_mtu_request"):
            model_from_json(PrinterProfile, payload)

    def test_ppa2l_profiles_default_to_tag_mode(self) -> None:
        ppa2l = self.catalog.require_profile("luck_ppa2l")
        ppa2lh = self.catalog.require_profile("luck_ppa2lh")

        self.assertEqual(ppa2l.default_paper_mode, PaperMode.TAG)
        self.assertEqual(ppa2lh.default_paper_mode, PaperMode.TAG)

    def test_catalog_rejects_missing_speed_for_speed_family(self) -> None:
        profile = model_from_json(PrinterProfile, _with_optional_speed(_profile_payload(), None))

        with self.assertRaisesRegex(ValueError, "requires speed defaults"):
            PrinterCatalog([profile], [])

    def test_catalog_rejects_missing_speed_for_speed_model_override(self) -> None:
        payload = _with_optional_speed(_profile_payload(), None)
        payload["protocol_default"]["type"] = "luck_normal"
        profile = model_from_json(PrinterProfile, payload)
        model = model_from_json(
            SupportedPrinterModel,
            _model_payload(
                model_key="demo",
                profile_key=profile.profile_key,
                protocol_family="tiny",
            ),
        )

        with self.assertRaisesRegex(ValueError, "requires speed defaults"):
            PrinterCatalog([profile], [model])

    def test_runtime_preset_key_is_scoped_to_profile(self) -> None:
        payload_a = _profile_payload("profile-a")
        payload_a["runtime_presets"] = [
            _runtime_preset_payload(key="shared", image_middle=111)
        ]
        payload_b = _profile_payload("profile-b")
        payload_b["runtime_presets"] = [
            _runtime_preset_payload(key="shared", image_middle=222)
        ]
        profile_a = model_from_json(PrinterProfile, payload_a)
        profile_b = model_from_json(PrinterProfile, payload_b)
        model_a = model_from_json(
            SupportedPrinterModel,
            _model_payload(
                model_key="model-a",
                profile_key="profile-a",
                prefixes=["A"],
                profile_runtime_preset_key="shared",
            ),
        )
        model_b = model_from_json(
            SupportedPrinterModel,
            _model_payload(
                model_key="model-b",
                profile_key="profile-b",
                prefixes=["B"],
                profile_runtime_preset_key="shared",
            ),
        )

        catalog = PrinterCatalog([profile_a, profile_b], [model_a, model_b])

        self.assertEqual(
            catalog.device_from_model("model-a").runtime_settings.preset.density.image.middle,
            111,
        )
        self.assertEqual(
            catalog.device_from_model("model-b").runtime_settings.preset.density.image.middle,
            222,
        )

    def test_first_match_wins_for_mac_suffix_rules(self) -> None:
        shared_profile = model_from_json(PrinterProfile, _profile_payload("shared"))
        models = [
            model_from_json(
                SupportedPrinterModel,
                _model_payload(
                    model_key="mac59",
                    profile_key="shared",
                    protocol_family="v5x",
                    prefixes=["MX05"],
                    mac_suffixes=["59"],
                ),
            ),
            model_from_json(
                SupportedPrinterModel,
                _model_payload(
                    model_key="default",
                    profile_key="shared",
                    protocol_family="v5g",
                    prefixes=["MX05"],
                ),
            ),
        ]
        catalog = PrinterCatalog([shared_profile], models)

        resolved = catalog.detect_device("MX05-ABCD", "AA:BB:CC:DD:EE:59")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.profile_key, "shared")
        self.assertEqual(resolved.protocol_family, ProtocolFamily.V5X)
        self.assertEqual(resolved.model_key, "mac59")

    def test_device_from_key_resolves_model_key_or_public_detection_name(self) -> None:
        by_model_key = self.catalog.device_from_key("luck_a2")
        by_detection_name = self.catalog.device_from_key("PPA2L")

        self.assertEqual(by_model_key.model_key, "luck_a2")
        self.assertEqual(by_detection_name.model_key, "luck_ppa2l")
        self.assertEqual(by_detection_name.display_name, "PPA2L")

    def test_device_from_key_rejects_ambiguous_public_detection_name(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "MX10.*yt01_mac59.*mx10"):
            self.catalog.device_from_key("MX10")

    def test_device_from_key_does_not_resolve_profile_keys(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unknown printer model or detection name"):
            self.catalog.device_from_key("v5g_small_203")

    def test_direct_profiles_resolve_without_alias_semantics(self) -> None:
        resolved = self.catalog.detect_device("X6H-1234")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.profile_key, "x6h")
        self.assertEqual(resolved.protocol_family, ProtocolFamily.TINY)
        self.assertEqual(resolved.image_pipeline.encoding, ImageEncoding.TINY_RLE)

        dl_x7pro = self.catalog.detect_device("DL_X7Pro-1234")
        self.assertIsNotNone(dl_x7pro)
        self.assertEqual(dl_x7pro.profile_key, "dl_x7pro")
        self.assertEqual(dl_x7pro.protocol_family, ProtocolFamily.TINY)
        self.assertEqual(dl_x7pro.profile.print_size, 1280)
        self.assertEqual(dl_x7pro.profile.dev_dpi, 300)

        p4 = self.catalog.detect_device("P4-1234")
        self.assertIsNotNone(p4)
        self.assertEqual(p4.profile_key, "p4")
        self.assertEqual(p4.protocol_family, ProtocolFamily.TINY)
        self.assertEqual(p4.protocol_variant, "line_eight")
        self.assertEqual(p4.profile.paper_size, 1600)
        self.assertEqual(p4.profile.print_size, 1728)
        self.assertEqual(p4.profile.width, 1600)

    def test_tiny_profiles_keep_source_defaults(self) -> None:
        d1 = self.catalog.require_profile("d1")
        self.assertEqual(d1.energy.image.low, 5000)
        self.assertEqual(d1.energy.image.middle, 5000)
        self.assertEqual(d1.energy.image.high, 5000)
        self.assertEqual(d1.energy.text.middle, 8000)

        ht0125 = self.catalog.detect_device("HT0125-ABCD")
        self.assertIsNotNone(ht0125)
        self.assertEqual(ht0125.profile_key, "d1")
        self.assertEqual(ht0125.profile.energy.image.middle, 5000)
        self.assertEqual(ht0125.profile.energy.text.middle, 8000)

        label_printer = self.catalog.require_profile("label_printer")
        self.assertEqual(label_printer.energy.image.middle, 1400)
        self.assertEqual(label_printer.energy.text.middle, 1400)

        tiny_15p3 = self.catalog.require_profile("15p3")
        self.assertEqual(tiny_15p3.energy.image.middle, 5000)
        self.assertEqual(tiny_15p3.energy.text.middle, 8000)

        self.assertEqual(self.catalog.require_profile("gt08").energy.image.low, 5000)
        self.assertEqual(self.catalog.require_profile("gt09").energy.image.low, 5000)
        self.assertEqual(self.catalog.require_profile("x8").energy.image.low, 5000)

        gb01 = self.catalog.require_profile("gb01")
        self.assertEqual(gb01.energy.image.low, 8000)
        self.assertEqual(gb01.energy.image.middle, 12000)
        self.assertEqual(gb01.energy.image.high, 17500)
        self.assertEqual(gb01.energy.text.middle, 0)

        x16 = self.catalog.detect_device("X16-ABCD")
        self.assertIsNotNone(x16)
        self.assertEqual(x16.profile_key, "x16")
        self.assertFalse(x16.profile.can_print_label)
        self.assertEqual(x16.profile.energy.image.middle, 5000)

    def test_tiny_special_protocol_variants_are_modeled(self) -> None:
        x9 = self.catalog.detect_device("X9-38CC")
        self.assertIsNotNone(x9)
        self.assertEqual(x9.profile_key, "x9")
        self.assertEqual(x9.protocol_variant, "line_eight")
        self.assertEqual(x9.profile.width, 1600)
        self.assertEqual(x9.profile.left_padding_pixels, 32)
        self.assertEqual(x9.profile.effective_left_padding_pixels, 32)
        self.assertEqual(x9.profile.a4_sheet_max_height, 2460)
        self.assertEqual(
            PrinterProtocol(x9).supported_paper_modes(),
            (PaperMode.PLAIN, PaperMode.A4_SHEET),
        )

        jxm800 = self.catalog.detect_device("GG-D2100-1234")
        self.assertIsNotNone(jxm800)
        self.assertEqual(jxm800.profile_key, "jxm800")
        self.assertEqual(jxm800.protocol_family, ProtocolFamily.TINY_PREFIXED)
        self.assertEqual(jxm800.protocol_variant, "esc_star_eight")
        self.assertEqual(
            PrinterProtocol(jxm800).supported_paper_modes(),
            (PaperMode.PLAIN, PaperMode.A4_SHEET),
        )

        ly10 = self.catalog.detect_device("LY10-1234")
        self.assertIsNotNone(ly10)
        self.assertEqual(ly10.profile_key, "ly10")
        self.assertEqual(ly10.protocol_family, ProtocolFamily.TINY_PREFIXED)
        self.assertEqual(ly10.protocol_variant, "esc_star")
        self.assertEqual(PrinterProtocol(ly10).supported_paper_modes(), ())

        professional = self.catalog.detect_device("CTP100LG-1234")
        self.assertIsNotNone(professional)
        self.assertEqual(professional.profile_key, "professional_printer")
        self.assertEqual(professional.protocol_variant, "professional")
        self.assertEqual(
            PrinterProtocol(professional).supported_paper_modes(),
            (PaperMode.PLAIN, PaperMode.A4_SHEET),
        )

    def test_origin_app_packages_keep_conflicting_names_explicit(self) -> None:
        tiny_p1 = self.catalog.detect_model("P1-")
        eleph_p1 = self.catalog.detect_model("P1_")
        dck_d1 = self.catalog.detect_model("C21")

        self.assertIsInstance(tiny_p1, SupportedModelMatch)
        self.assertIsInstance(eleph_p1, SupportedModelMatch)
        self.assertIsInstance(dck_d1, SupportedModelMatch)
        assert isinstance(tiny_p1, SupportedModelMatch)
        assert isinstance(eleph_p1, SupportedModelMatch)
        assert isinstance(dck_d1, SupportedModelMatch)
        self.assertEqual(tiny_p1.model.origin_app_packages[0], "com.frogtosea.tinyPrint")
        self.assertEqual(eleph_p1.model.origin_app_packages[0], "com.sandu.JxPrinter")
        self.assertEqual(eleph_p1.model.origin_app_packages[1], "com.fyhd.toprint")
        self.assertEqual(dck_d1.model.origin_app_packages[0], "com.fun.mxw")
        self.assertEqual(dck_d1.model.origin_app_packages[1], "com.bleem.liugm")

    def test_old_small_bucket_uses_v5g_and_mac59_switches_family_only(self) -> None:
        normal = self.catalog.detect_device("MX05-ABCD", "AA:BB:CC:DD:EE:58")
        mac59 = self.catalog.detect_device("MX05-ABCD", "AA:BB:CC:DD:EE:59")

        self.assertIsNotNone(normal)
        self.assertIsNotNone(mac59)
        self.assertEqual(normal.profile_key, "v5g_small_203")
        self.assertEqual(mac59.profile_key, "v5g_small_203")
        self.assertEqual(normal.protocol_family, ProtocolFamily.V5G)
        self.assertEqual(mac59.protocol_family, ProtocolFamily.V5X)
        self.assert_runtime_settings(
            normal,
            variant="mx06",
            preset_key="mx06",
            d2_status=True,
        )

    def test_old_small_bucket_shared_names_resolve_to_shared_profile(self) -> None:
        normal = self.catalog.detect_device("XOPOPPY", "AA:BB:CC:DD:EE:58")
        mac59 = self.catalog.detect_device("XOPOPPY", "AA:BB:CC:DD:EE:59")

        self.assertIsNotNone(normal)
        self.assertIsNotNone(mac59)
        self.assertEqual(normal.profile_key, "v5g_small_203")
        self.assertEqual(mac59.profile_key, "v5g_small_203")
        self.assertEqual(normal.protocol_family, ProtocolFamily.V5G)
        self.assertEqual(mac59.protocol_family, ProtocolFamily.V5X)

    def test_dynamic_v5g_rules_expose_helper_metadata(self) -> None:
        mx06 = self.catalog.detect_device("MX06-ABCD", "AA:BB:CC:DD:EE:58")
        mx08 = self.catalog.detect_device("MX08-ABCD", "AA:BB:CC:DD:EE:58")
        mx09 = self.catalog.detect_device("MX09-ABCD", "AA:BB:CC:DD:EE:58")
        mx10 = self.catalog.detect_device("MX10-ABCD", "AA:BB:CC:DD:EE:58")
        pd01 = self.catalog.detect_device("PD01-ABCD", "AA:BB:CC:DD:EE:58")
        xopoppy = self.catalog.detect_device("XOPOPPY-ABCD", "AA:BB:CC:DD:EE:58")
        mx13 = self.catalog.detect_device("MX13-ABCD", "AA:BB:CC:DD:EE:58")
        mxw010 = self.catalog.detect_device("MXW010-ABCD", "AA:BB:CC:DD:EE:58")

        self.assertIsNotNone(mx06)
        self.assert_runtime_settings(mx06, variant="mx06", preset_key="mx06", d2_status=True)

        self.assertIsNotNone(mx08)
        self.assert_runtime_settings(mx08, variant="d2", preset_key="mx08", d2_status=True)

        self.assertIsNotNone(mx09)
        self.assert_runtime_settings(
            mx09,
            variant="d2",
            preset_key="mx09",
            d2_status=True,
            didian_status=True,
        )

        self.assertIsNotNone(mx10)
        self.assert_runtime_settings(mx10, variant="mx10", preset_key="mx10_mx06", d2_status=True)

        self.assertIsNotNone(pd01)
        self.assert_runtime_settings(pd01, variant="pd01", preset_key="pd01_mx11")

        self.assertIsNotNone(xopoppy)
        self.assert_runtime_settings(xopoppy, variant="mx10", preset_key="xopoppy")

        self.assertIsNotNone(mx13)
        self.assert_runtime_settings(mx13, variant="mx10", preset_key="xopoppy")

        self.assertIsNotNone(mxw010)
        self.assert_runtime_settings(mxw010, variant="mx10", preset_key="mx10_control")

    def test_printer_config_roundtrip_preserves_runtime_fields(self) -> None:
        resolved = self.catalog.detect_device("MX10-ABCD", "AA:BB:CC:DD:EE:58")

        self.assertIsNotNone(resolved)
        printer_config = self.catalog.serialize_printer_config(resolved)
        rebuilt = self.catalog.device_from_printer_config(printer_config)

        self.assertEqual(rebuilt.display_name, resolved.display_name)
        self.assertEqual(rebuilt.profile_key, resolved.profile_key)
        self.assertEqual(rebuilt.protocol_family, resolved.protocol_family)
        self.assertEqual(rebuilt.image_pipeline, resolved.image_pipeline)
        self.assert_runtime_settings(
            rebuilt,
            variant=resolved.runtime_settings.control_algorithm,
            preset_key=resolved.runtime_settings.preset_key,
            d2_status=resolved.runtime_settings.capabilities.d2_status,
            didian_status=resolved.runtime_settings.capabilities.didian_status,
        )
        self.assertEqual(rebuilt.address, resolved.address)
        self.assertEqual(rebuilt.transport_badge, resolved.transport_badge)

    def test_printer_config_runtime_density_override_updates_runtime_preset_only(self) -> None:
        resolved = self.catalog.detect_device("MX10-ABCD", "AA:BB:CC:DD:EE:58")
        self.assertIsNotNone(resolved)
        printer_config = self.catalog.serialize_printer_config(resolved)
        printer_config["runtime_overrides"]["density"] = {
            "image": {"middle": 177},
        }

        rebuilt = self.catalog.device_from_printer_config(printer_config)

        self.assertIsNotNone(rebuilt.runtime_settings)
        self.assertIsNotNone(rebuilt.runtime_settings.preset)
        self.assertEqual(rebuilt.runtime_settings.preset.key, "mx10_mx06")
        self.assertIsNotNone(rebuilt.runtime_settings.preset.density)
        self.assertEqual(rebuilt.runtime_settings.preset.density.image.middle, 177)
        self.assertIsNone(rebuilt.profile.density)

    def test_printer_config_model_key_is_fallback_for_protocol_and_runtime(self) -> None:
        resolved = self.catalog.device_from_key("mx10")
        printer_config = self.catalog.serialize_printer_config(resolved)
        self.assertEqual(printer_config["model_key"], "mx10")
        del printer_config["profile_overrides"]["protocol_default"]
        del printer_config["runtime_overrides"]

        rebuilt = self.catalog.device_from_printer_config(printer_config)

        self.assertEqual(rebuilt.model_key, "mx10")
        self.assertEqual(rebuilt.protocol_family, resolved.protocol_family)
        self.assertEqual(rebuilt.protocol_variant, resolved.protocol_variant)
        self.assertIsNotNone(rebuilt.runtime_settings)
        self.assertEqual(rebuilt.runtime_settings.preset_key, "mx10_mx06")

    def test_printer_config_model_key_is_fallback_for_protocol_override(self) -> None:
        resolved = self.catalog.device_from_key("c21")
        printer_config = self.catalog.serialize_printer_config(resolved)
        self.assertEqual(printer_config["model_key"], "c21")
        del printer_config["profile_overrides"]["protocol_default"]

        rebuilt = self.catalog.device_from_printer_config(printer_config)

        self.assertEqual(rebuilt.model_key, "c21")
        self.assertEqual(rebuilt.protocol_family, resolved.protocol_family)
        self.assertEqual(rebuilt.protocol_family, ProtocolFamily.DCK)

    def test_printer_config_rejects_runtime_preset_from_other_profile(self) -> None:
        resolved = self.catalog.device_from_key("mx10")
        printer_config = self.catalog.serialize_printer_config(resolved)
        printer_config["runtime_overrides"]["preset_key"] = "demo"

        with self.assertRaisesRegex(RuntimeError, "Unknown runtime preset 'demo' for profile"):
            self.catalog.device_from_printer_config(printer_config)

    def test_printer_config_roundtrip_preserves_protocol_variant(self) -> None:
        resolved = self.catalog.detect_device("QIRUI_Q2_1234")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.protocol_variant, "qirui_q2")
        printer_config = self.catalog.serialize_printer_config(resolved)
        rebuilt = self.catalog.device_from_printer_config(printer_config)

        self.assertEqual(rebuilt.profile_key, resolved.profile_key)
        self.assertEqual(rebuilt.protocol_family, resolved.protocol_family)
        self.assertEqual(rebuilt.protocol_variant, resolved.protocol_variant)
        self.assertEqual(rebuilt.image_pipeline, resolved.image_pipeline)

    def test_printer_config_profile_overrides_fall_back_to_base_profile(self) -> None:
        base = self.catalog.device_from_profile("gt01")
        printer_config = self.catalog.serialize_printer_config(base)
        printer_config["profile_overrides"] = {
            "stream": {
                "delay_ms": 9,
            },
        }

        rebuilt = self.catalog.device_from_printer_config(printer_config)

        self.assertEqual(rebuilt.profile.stream.chunk_size, base.profile.stream.chunk_size)
        self.assertEqual(rebuilt.profile.stream.delay_ms, 9)
        self.assertEqual(rebuilt.protocol_family, base.protocol_family)
        self.assertEqual(rebuilt.image_pipeline, base.image_pipeline)

    def test_device_from_printer_config_rejects_unknown_protocol_variant(self) -> None:
        base = self.catalog.device_from_profile("luck_a40")
        printer_config = self.catalog.serialize_printer_config(base)
        printer_config["profile_overrides"]["protocol_default"]["packets_type"] = "not_a_variant"

        with self.assertRaisesRegex(
            RuntimeError,
            "luck_normal_a4 does not support protocol variant 'not_a_variant'",
        ):
            self.catalog.device_from_printer_config(printer_config)

    def test_luck_a49h_uses_compressed_a4_pipeline(self) -> None:
        resolved = self.catalog.detect_device("APA49H_1234")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.profile_key, "luck_a49h")
        self.assertEqual(resolved.protocol_family, ProtocolFamily.LUCK_NORMAL_A4)
        self.assertEqual(resolved.profile.dev_dpi, 300)
        self.assertEqual(resolved.image_pipeline.encoding, ImageEncoding.LUCK_NORMAL_COMPRESSED)

    def test_exact_name_rules_cover_x6_without_shadowing_x6h(self) -> None:
        x6 = self.catalog.detect_device("X6", "AA:BB:CC:DD:EE:58")
        x6_mac59 = self.catalog.detect_device("X6", "AA:BB:CC:DD:EE:59")
        x6h = self.catalog.detect_device("X6H-1234", "AA:BB:CC:DD:EE:59")

        self.assertIsNotNone(x6)
        self.assertIsNotNone(x6_mac59)
        self.assertIsNotNone(x6h)
        self.assertEqual(x6.profile_key, "v5g_small_203")
        self.assertEqual(x6.protocol_family, ProtocolFamily.V5G)
        self.assertEqual(x6_mac59.profile_key, "v5g_small_203")
        self.assertEqual(x6_mac59.protocol_family, ProtocolFamily.V5X)
        self.assertEqual(x6h.profile_key, "x6h")
        self.assertEqual(x6h.protocol_family, ProtocolFamily.TINY)

    def test_v5x_exact_name_rules_do_not_shadow_other_x_series_profiles(self) -> None:
        x1 = self.catalog.detect_device("X1")
        x2 = self.catalog.detect_device("X2")
        x103h = self.catalog.detect_device("X103H")
        x2h = self.catalog.detect_device("X2H")

        self.assertIsNotNone(x1)
        self.assertIsNotNone(x2)
        self.assertIsNotNone(x103h)
        self.assertIsNotNone(x2h)
        self.assertEqual(x1.profile_key, "v5x")
        self.assertEqual(x1.protocol_family, ProtocolFamily.V5X)
        self.assertEqual(x2.profile_key, "v5x")
        self.assertEqual(x2.protocol_family, ProtocolFamily.V5X)
        self.assertEqual(x103h.profile_key, "x6h")
        self.assertEqual(x103h.protocol_family, ProtocolFamily.TINY)
        self.assertEqual(x2h.profile_key, "x6h")
        self.assertEqual(x2h.protocol_family, ProtocolFamily.TINY)

    def test_case_sensitive_direct_rules_keep_mixed_case_profiles_distinct(self) -> None:
        expected = {
            "SC03H-ABCD": "fc02",
            "SC03h-ABCD": "d1",
            "X103H-ABCD": "x6h",
            "X103h-ABCD": "d1",
            "X2H-ABCD": "x6h",
            "X2h-ABCD": "d1",
            "X5H-ABCD": "x6h",
            "X5h-ABCD": "d1",
            "X6H-ABCD": "x6h",
            "X6h-ABCD": "d1",
            "X7H-ABCD": "x6h",
            "X7h-ABCD": "d1",
        }

        for name, profile_key in expected.items():
            with self.subTest(name=name):
                resolved = self.catalog.detect_device(name)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.profile_key, profile_key)
                self.assertEqual(resolved.protocol_family, ProtocolFamily.TINY)

    def test_tiny_spacing_and_alias_corner_cases_still_resolve(self) -> None:
        expected = {
            " X101H-ABCD": ("x101h", ProtocolFamily.TINY),
            "X101H-ABCD": ("x101h", ProtocolFamily.TINY),
            "K06-ABCD": ("v5g_small_203", ProtocolFamily.V5G),
            "X2-ABCD": ("v5x", ProtocolFamily.V5X),
        }

        for name, (profile_key, family) in expected.items():
            with self.subTest(name=name):
                resolved = self.catalog.detect_device(name, "AA:BB:CC:DD:EE:58")
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.profile_key, profile_key)
                self.assertEqual(resolved.protocol_family, family)

    def test_case_insensitive_fallback_still_detects_lowercase_names(self) -> None:
        resolved = self.catalog.detect_device("sc03h-abcd")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.profile_key, "fc02")
        self.assertEqual(resolved.protocol_family, ProtocolFamily.TINY)

    def test_ai01_resolves_to_v5x_family(self) -> None:
        ai01 = self.catalog.detect_device("AI01-ABCD")

        self.assertIsNotNone(ai01)
        self.assertEqual(ai01.profile_key, "ai01")
        self.assertEqual(ai01.protocol_family, ProtocolFamily.V5X)

    def test_luck_normal_rules_resolve_the_source_backed_models(self) -> None:
        expected = {
            "PPA2_1234": ("luck_a2", ProtocolFamily.LUCK_NORMAL, None, 384),
            "A2": ("luck_a2", ProtocolFamily.LUCK_NORMAL, None, 384),
            "A2_EY48D": ("luck_a2", ProtocolFamily.LUCK_NORMAL, None, 384),
            "A2_LYiN48D_ITSR": ("luck_a2", ProtocolFamily.LUCK_NORMAL, None, 384),
            "PPA2H_1234": ("luck_a2h", ProtocolFamily.LUCK_NORMAL, None, 576),
            "A2H": ("luck_a2h", ProtocolFamily.LUCK_NORMAL, None, 576),
            "A2_LYiN48DH": ("luck_a2h", ProtocolFamily.LUCK_NORMAL, None, 576),
            "PPA2L_1234": ("luck_ppa2l", ProtocolFamily.LUCK_NORMAL, "lujiang_normal", 384),
            "PPA2L": ("luck_ppa2l", ProtocolFamily.LUCK_NORMAL, "lujiang_normal", 384),
            "PPA2LH_1234": ("luck_ppa2lh", ProtocolFamily.LUCK_NORMAL, "lujiang_normal_h", 576),
            "PPA2LH": ("luck_ppa2lh", ProtocolFamily.LUCK_NORMAL, "lujiang_normal_h", 576),
            "A40_1234": ("luck_a40", ProtocolFamily.LUCK_NORMAL_A4, None, 1728),
            "A40": ("luck_a40", ProtocolFamily.LUCK_NORMAL_A4, None, 1728),
            "APA40_1234": ("luck_lujiang_a4", ProtocolFamily.LUCK_NORMAL_A4, "lujiang_a4", 1728),
            "APA41_1234": ("luck_lujiang_a4_dense", ProtocolFamily.LUCK_NORMAL_A4, "lujiang_a4", 1728),
            "APA42_1234": ("luck_lujiang_a4", ProtocolFamily.LUCK_NORMAL_A4, "lujiang_a4", 1728),
            "APA43_1234": ("luck_lujiang_a4", ProtocolFamily.LUCK_NORMAL_A4, "lujiang_a4", 1728),
            "APA49_1234": ("luck_lujiang_a4_dense", ProtocolFamily.LUCK_NORMAL_A4, "lujiang_a4", 1728),
            "A49": ("luck_lujiang_a4_dense", ProtocolFamily.LUCK_NORMAL_A4, "lujiang_a4", 1728),
            "APA49H_1234": ("luck_a49h", ProtocolFamily.LUCK_NORMAL_A4, "a49h", 2496),
            "ITP05": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "ITP05H": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "DYA46": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "DP_ITP05_1234": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "ITP06": ("luck_a4_compressed_tattoo_96_dense", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64_endline96", 1728),
            "DYA49": ("luck_a4_compressed_tattoo_96_dense", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64_endline96", 1728),
            "DP_ITP06_1234": ("luck_a4_compressed_tattoo_96_dense", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64_endline96", 1728),
            "APA46Y": ("luck_a4_compressed_tattoo_96", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64_endline96", 1728),
            "APA46Y_1234": ("luck_a4_compressed_tattoo_96", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64_endline96", 1728),
            "TPA46": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "TPA46_1234": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "TPA46Pro": ("luck_a4_compressed_tattoo_96_dense", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64_endline96", 1728),
            "TPA46Pro_1234": ("luck_a4_compressed_tattoo_96_dense", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64_endline96", 1728),
            "DP_A4_1234": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "DP-A4_1234": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "DP_8038_1234": ("luck_a4_compressed_tattoo", ProtocolFamily.LUCK_NORMAL_A4, "a4_tattoo_64", 1728),
            "APL86": ("luck_apl86", ProtocolFamily.LUCK_NORMAL_A4, "apl86", 1728),
            "APL86_1234": ("luck_apl86", ProtocolFamily.LUCK_NORMAL_A4, "apl86", 1728),
            "L86": ("luck_apl86", ProtocolFamily.LUCK_NORMAL_A4, "apl86", 1728),
            "APL86H": ("luck_apl86h", ProtocolFamily.LUCK_NORMAL_A4, "apl86", 2496),
            "APL86H_1234": ("luck_apl86h", ProtocolFamily.LUCK_NORMAL_A4, "apl86", 2496),
            "APL86HL": ("luck_apl86h", ProtocolFamily.LUCK_NORMAL_A4, "apl86", 2496),
            "U8": ("luck_u8", ProtocolFamily.LUCK_NORMAL_A4, "u8", 1728),
            "U8_1234": ("luck_u8", ProtocolFamily.LUCK_NORMAL_A4, "u8", 1728),
            "D80": ("luck_d80", ProtocolFamily.LUCK_NORMAL_A4, "d80", 1728),
            "DP_D80_1234": ("luck_d80", ProtocolFamily.LUCK_NORMAL_A4, "d80", 1728),
            "DP-D80_1234": ("luck_d80", ProtocolFamily.LUCK_NORMAL_A4, "d80", 1728),
            "E80_1234": ("luck_d80", ProtocolFamily.LUCK_NORMAL_A4, "d80", 1728),
            "CASA-01_1234": ("luck_d80", ProtocolFamily.LUCK_NORMAL_A4, "d80", 1728),
            "PeriPage_A40": ("luck_d80", ProtocolFamily.LUCK_NORMAL_A4, "d80", 1728),
            "DYD80": ("luck_d80", ProtocolFamily.LUCK_NORMAL_A4, "d80", 1728),
            "DYD80H": ("luck_d80h", ProtocolFamily.LUCK_NORMAL_A4, "d80h", 2496),
            "DP_D80H_1234": ("luck_d80h", ProtocolFamily.LUCK_NORMAL_A4, "d80h", 2496),
            "A80H-HD": ("luck_a80h_way1", ProtocolFamily.LUCK_NORMAL_A4, "a80h_way1", 2496),
            "DP_A80H_1234": ("luck_a80h_way1", ProtocolFamily.LUCK_NORMAL_A4, "a80h_way1", 2496),
            "QIRUI_Q1_1234": ("luck_qirui_q1", ProtocolFamily.LUCK_NORMAL, "qirui_q1", 384),
            "QIRUI_Q2_1234": ("luck_qirui_q2", ProtocolFamily.LUCK_NORMAL, "qirui_q2", 576),
            "LuckP_A41_1234": ("luck_a41_luckp", ProtocolFamily.LUCK_NORMAL_A4, "luckp_a41", 1728),
            "LuckP_A42_1234": ("luck_a42_luckp", ProtocolFamily.LUCK_NORMAL_A4, "luckp_a42", 1728),
        }

        for name, (profile_key, family, protocol_variant, width) in expected.items():
            with self.subTest(name=name):
                resolved = self.catalog.detect_device(name)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.profile_key, profile_key)
                self.assertEqual(resolved.protocol_family, family)
                self.assertEqual(resolved.protocol_variant, protocol_variant)
                self.assertEqual(resolved.profile.width, width)
                if profile_key in {
                    "luck_lujiang_a4",
                    "luck_lujiang_a4_dense",
                    "luck_a49h",
                    "luck_a4_compressed_tattoo",
                    "luck_a4_compressed_tattoo_96",
                    "luck_a4_compressed_tattoo_96_dense",
                    "luck_u8",
                    "luck_apl86",
                    "luck_apl86h",
                    "luck_d80",
                    "luck_d80h",
                    "luck_a80h_way1",
                }:
                    self.assertEqual(resolved.image_pipeline.encoding, ImageEncoding.LUCK_NORMAL_COMPRESSED)
                else:
                    self.assertEqual(resolved.image_pipeline.encoding, ImageEncoding.LUCK_NORMAL_RAW)

    def test_luck_normal_rules_do_not_claim_variants_we_did_not_implement(self) -> None:
        for name in (
            "A49H",
            "DP_ITP05N_1234",
            "ITP05N",
            "DP_ITP06N_1234",
            "ITP06N",
            "D80H",
            "PCPS_D80_1234",
            "DP_A80_1234",
            "DP_A80S_1234",
            "DP_A80W_1234",
            "PD_A4",
            "GD-88_1234",
        ):
            with self.subTest(name=name):
                self.assertIsNone(self.catalog.detect_device(name))

    def test_specific_proxy_and_bucket_rules_are_not_shadowed(self) -> None:
        expected = {
            ("BQ95B", "AA:BB:CC:DD:EE:00"): ("v5g_small_203", ProtocolFamily.V5G),
            ("BQ95B", "AA:BB:CC:DD:EE:59"): ("v5g_small_203", ProtocolFamily.V5X),
            ("BQ95C", "AA:BB:CC:DD:EE:00"): ("v5g_small_203", ProtocolFamily.V5G),
            ("BQ95C", "AA:BB:CC:DD:EE:59"): ("v5g_small_203", ProtocolFamily.V5X),
            ("BQ06B", "AA:BB:CC:DD:EE:00"): ("v5g_small_203", ProtocolFamily.V5G),
            ("BQ06B", "AA:BB:CC:DD:EE:59"): ("v5g_small_203", ProtocolFamily.V5X),
        }

        for (name, address), (profile_key, family) in expected.items():
            with self.subTest(name=name, address=address):
                resolved = self.catalog.detect_device(name, address)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.profile_key, profile_key)
                self.assertEqual(resolved.protocol_family, family)

    def test_v5g_profiles_keep_source_backed_pipeline_and_density_cases(self) -> None:
        mx07 = self.catalog.require_profile("mx07")
        mx10 = self.catalog.require_profile("v5g_small_203")
        bq02 = self.catalog.require_profile("bq02")
        gt02 = self.catalog.require_profile("gt02_v5g")
        shared = self.catalog.require_profile("v5g_small_203")

        self.assertIsNotNone(mx07.density)
        self.assertEqual(mx07.density.image.high, 100)

        self.assertEqual(mx10.protocol_default.type, ProtocolFamily.V5G)
        self.assertIsNone(mx10.speed)
        self.assertEqual(mx10.post_print_feed_count, 1)
        self.assertEqual(mx10.energy.image.middle, 10000)
        self.assertEqual(mx10.energy.text.high, 20000)

        xopoppy_runtime = self.catalog.device_from_key("mx13_v5g")
        self.assertIsNotNone(xopoppy_runtime.runtime_settings)
        self.assertIsNotNone(xopoppy_runtime.runtime_settings.preset)
        self.assertIsNotNone(xopoppy_runtime.runtime_settings.preset.density)
        self.assertEqual(xopoppy_runtime.runtime_settings.preset.density.text.middle, 80)

        self.assertIsNotNone(bq02.density)
        self.assertEqual(bq02.density.text.high, 180)

        self.assertIsNotNone(gt02.density)
        self.assertEqual(gt02.density.image.middle, 110)
        self.assertEqual(gt02.density.text.high, 150)

        self.assertEqual(shared.protocol_default.type, ProtocolFamily.V5G)
        self.assertIsNone(shared.speed)
        self.assertEqual(shared.post_print_feed_count, 1)
        self.assertEqual(shared.energy.image.middle, 10000)
        self.assertEqual(shared.energy.text.high, 20000)

    def test_proxy_rules_resolve_to_profiles_not_alias_donors(self) -> None:
        jk01 = self.catalog.detect_device("JK01-ABCD")
        c21 = self.catalog.detect_device("C21-ABCD")
        ytb01 = self.catalog.detect_device("YTB01-ABCD")

        self.assertIsNotNone(jk01)
        self.assertEqual(jk01.profile_key, "v5x")
        self.assertEqual(jk01.protocol_family, ProtocolFamily.V5X)

        self.assertIsNotNone(c21)
        self.assertEqual(c21.profile_key, "d1")
        self.assertEqual(c21.protocol_family, ProtocolFamily.DCK)

        self.assertIsNotNone(ytb01)
        self.assertEqual(ytb01.profile_key, "ytb01")
        self.assertEqual(ytb01.protocol_family, ProtocolFamily.V5C)

    def test_derived_names_map_to_final_profiles(self) -> None:
        expected = {
            "MXTP-100-ABCD": "v5g_small_203",
            "MXPC-100-ABCD": "v5g_small_203",
            "LY10-ABCD": "ly10",
            "PD01-ABCD": "v5g_small_203",
            "AZ-P2108X-ABCD": "v5g_small_203",
            "MX12-ABCD": "v5g_small_203",
            "MX13-ABCD": "v5g_small_203",
            "MX07-ABCD": "mx07",
            "XOPOPPY-ABCD": "v5g_small_203",
            "PR20-ABCD": "xw001",
            "XW001-ABCD": "xw001",
            "PR25-ABCD": "m01",
            "XW003-ABCD": "m01",
            "PR30-ABCD": "pr30",
            "XW002-ABCD": "xw002",
            "XW004-ABCD": "pr35",
            "XW005-ABCD": "gt08",
            "XW006-ABCD": "pr89",
            "XW007-ABCD": "pr893",
            "XW008-ABCD": "pr02",
            "XW009-ABCD": "m01",
            "BQ02-ABCD": "bq02",
            "BQ03-ABCD": "bq02",
            "BQ17-ABCD": "bq02",
            "MINIPRINTER": "gt02_v5g",
            "JL-BR22": "gt02_v5g",
            "CYLOBTPrinter": "v5g_small_203",
            "EWTTO ET-Z0499": "v5g_small_203",
            "GV-MA211-ABCD": "v5g_small_203",
            "X6": "v5g_small_203",
            "K06-ABCD": "v5g_small_203",
            "K06": "v5g_small_203",
            "X2-ABCD": "v5x",
        }

        for name, profile_key in expected.items():
            with self.subTest(name=name):
                resolved = self.catalog.detect_device(name, "AA:BB:CC:DD:EE:58")
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.profile_key, profile_key)

    def test_fun_print_mx06_derived_names_use_runtime_density_defaults(self) -> None:
        for name in ("MXTP-100-ABCD", "CYLO BT PRINTER", "EWTTO ET-Z0499"):
            with self.subTest(name=name):
                resolved = self.catalog.detect_device(name, "AA:BB:CC:DD:EE:58")

                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.profile_key, "v5g_small_203")
                self.assertEqual(resolved.protocol_family, ProtocolFamily.V5G)
                self.assert_runtime_settings(
                    resolved,
                    variant="mx06",
                    preset_key="mx06",
                    d2_status=True,
                )

    def test_old_ibleem_proxy_buckets_no_longer_resolve(self) -> None:
        for name in (
            "P100-ABCD",
            "P100S-ABCD",
            "LP100-ABCD",
            "LP220S",
            "YINTIBAO-V5PRO",
            "MP300S",
            "M08F-ABCD",
            "TP81-ABCD",
            "TP84-ABCD",
            "M832-ABCD",
            "M836-ABCD",
            "Q302-ABCD",
            "Q580-ABCD",
            "MXW-A4-ABCD",
        ):
            with self.subTest(name=name):
                self.assertIsNone(self.catalog.detect_device(name))


if __name__ == "__main__":
    unittest.main()
