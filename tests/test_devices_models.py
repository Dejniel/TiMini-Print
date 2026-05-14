from __future__ import annotations

import unittest

from tests.helpers import reset_registry_cache
from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import DetectionRule
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding, PaperMode
from timiniprint.raster import PixelFormat


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
        "can_change_mtu": False,
        "has_id": False,
        "use_spp": False,
        "can_print_label": False,
        "label_value": "",
        "back_paper_num": 0,
        "default_protocol_family": "legacy",
        "default_image_pipeline": {
            "formats": ["bw1"],
            "encoding": "legacy_raw",
        },
        "stream": {
            "chunk_size": 180,
            "delay_ms": 4,
        },
        "post_print_feed_count": 2,
        "tuning": {
            "speed": speed,
            "energy": {
                "image": {"low": 5000, "middle": 5000, "high": 5000},
                "text": {"low": 8000, "middle": 8000, "high": 8000},
            },
        },
    }


def _with_optional_speed(payload: dict, speed: dict | None) -> dict:
    payload = dict(payload)
    payload["tuning"] = dict(payload["tuning"])
    if speed is None:
        payload["tuning"].pop("speed", None)
    else:
        payload["tuning"]["speed"] = speed
    return payload


class DevicesModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_registry_cache()
        self.catalog = PrinterCatalog.load()

    def test_catalog_loads_profiles_and_rules(self) -> None:
        self.assertGreater(len(self.catalog.profiles), 0)
        self.assertGreater(len(self.catalog.rules), 0)
        profile = self.catalog.require_profile("x6h")
        self.assertEqual(profile.stream.chunk_size, 180)
        self.assertEqual(profile.stream.delay_ms, 4)

    def test_parse_profile_rejects_non_positive_stream_chunk_size(self) -> None:
        payload = _profile_payload()
        payload["stream"]["chunk_size"] = 0
        with self.assertRaisesRegex(ValueError, "stream.chunk_size"):
            PrinterCatalog._parse_profile(payload)

    def test_parse_profile_allows_missing_speed_for_non_speed_family(self) -> None:
        payload = _with_optional_speed(_profile_payload(), None)
        payload["default_protocol_family"] = "luck_normal"

        profile = PrinterCatalog._parse_profile(payload)

        self.assertIsNone(profile.speed)

    def test_parse_profile_accepts_default_paper_mode(self) -> None:
        payload = _with_optional_speed(_profile_payload(), None)
        payload["default_protocol_family"] = "luck_normal"
        payload["default_paper_mode"] = "tag"

        profile = PrinterCatalog._parse_profile(payload)

        self.assertEqual(profile.default_paper_mode, PaperMode.TAG)

    def test_ppa2l_profiles_default_to_tag_mode(self) -> None:
        ppa2l = self.catalog.require_profile("luck_ppa2l")
        ppa2lh = self.catalog.require_profile("luck_ppa2lh")

        self.assertEqual(ppa2l.default_paper_mode, PaperMode.TAG)
        self.assertEqual(ppa2lh.default_paper_mode, PaperMode.TAG)

    def test_catalog_rejects_missing_speed_for_speed_family(self) -> None:
        profile = PrinterCatalog._parse_profile(_with_optional_speed(_profile_payload(), None))

        with self.assertRaisesRegex(ValueError, "requires speed tuning"):
            PrinterCatalog([profile], [])

    def test_catalog_rejects_missing_speed_for_speed_rule_override(self) -> None:
        payload = _with_optional_speed(_profile_payload(), None)
        payload["default_protocol_family"] = "luck_normal"
        profile = PrinterCatalog._parse_profile(payload)
        rule = DetectionRule(
            rule_key="demo",
            prefixes=("DEMO",),
            exact_names=(),
            profile_key=profile.profile_key,
            protocol_family=ProtocolFamily.LEGACY,
        )

        with self.assertRaisesRegex(ValueError, "requires speed tuning"):
            PrinterCatalog([profile], [rule])

    def test_first_match_wins_for_mac_suffix_rules(self) -> None:
        shared_profile = PrinterCatalog._parse_profile(_profile_payload("shared"))
        rules = [
            DetectionRule(
                rule_key="mac59",
                prefixes=("MX05",),
                exact_names=(),
                profile_key="shared",
                protocol_family=ProtocolFamily.V5X,
                mac_suffixes=("59",),
            ),
            DetectionRule(
                rule_key="default",
                prefixes=("MX05",),
                exact_names=(),
                profile_key="shared",
                protocol_family=ProtocolFamily.V5G,
            ),
        ]
        catalog = PrinterCatalog([shared_profile], rules)

        resolved = catalog.detect_device("MX05-ABCD", "AA:BB:CC:DD:EE:59")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.profile_key, "shared")
        self.assertEqual(resolved.protocol_family, ProtocolFamily.V5X)
        self.assertEqual(resolved.detection_rule_key, "mac59")

    def test_direct_profiles_resolve_without_alias_semantics(self) -> None:
        resolved = self.catalog.detect_device("X6H-1234")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.profile_key, "x6h")
        self.assertEqual(resolved.protocol_family, ProtocolFamily.LEGACY)
        self.assertEqual(resolved.image_pipeline.encoding, ImageEncoding.LEGACY_RLE)

        dl_x7pro = self.catalog.detect_device("DL_X7Pro-1234")
        self.assertIsNotNone(dl_x7pro)
        self.assertEqual(dl_x7pro.profile_key, "dl_x7pro")
        self.assertEqual(dl_x7pro.protocol_family, ProtocolFamily.LEGACY)
        self.assertEqual(dl_x7pro.profile.print_size, 1280)
        self.assertEqual(dl_x7pro.profile.dev_dpi, 300)

        p4 = self.catalog.detect_device("P4-1234")
        self.assertIsNotNone(p4)
        self.assertEqual(p4.profile_key, "p4")
        self.assertEqual(p4.protocol_family, ProtocolFamily.LEGACY)
        self.assertEqual(p4.profile.paper_size, 1600)
        self.assertEqual(p4.profile.print_size, 1728)

    def test_old_small_bucket_uses_v5g_and_mac59_switches_family_only(self) -> None:
        normal = self.catalog.detect_device("MX05-ABCD", "AA:BB:CC:DD:EE:58")
        mac59 = self.catalog.detect_device("MX05-ABCD", "AA:BB:CC:DD:EE:59")

        self.assertIsNotNone(normal)
        self.assertIsNotNone(mac59)
        self.assertEqual(normal.profile_key, "mx05")
        self.assertEqual(mac59.profile_key, "mx05")
        self.assertEqual(normal.protocol_family, ProtocolFamily.V5G)
        self.assertEqual(mac59.protocol_family, ProtocolFamily.V5X)

    def test_old_small_bucket_shared_names_resolve_to_shared_profile(self) -> None:
        normal = self.catalog.detect_device("XOPOPPY", "AA:BB:CC:DD:EE:58")
        mac59 = self.catalog.detect_device("XOPOPPY", "AA:BB:CC:DD:EE:59")

        self.assertIsNotNone(normal)
        self.assertIsNotNone(mac59)
        self.assertEqual(normal.profile_key, "xopoppy")
        self.assertEqual(mac59.profile_key, "xopoppy")
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
        self.assertEqual(mx06.runtime_variant, "mx06")
        self.assertIsNotNone(mx06.runtime_density_profile)
        self.assertEqual(mx06.runtime_density_profile.profile_key, "mx06")

        self.assertIsNotNone(mx08)
        self.assertEqual(mx08.runtime_variant, "d2")
        self.assertIsNotNone(mx08.runtime_density_profile)
        self.assertEqual(mx08.runtime_density_profile.profile_key, "mx08")

        self.assertIsNotNone(mx09)
        self.assertEqual(mx09.runtime_variant, "d2")
        self.assertIsNotNone(mx09.runtime_density_profile)
        self.assertEqual(mx09.runtime_density_profile.profile_key, "mx09")

        self.assertIsNotNone(mx10)
        self.assertEqual(mx10.runtime_variant, "mx10")
        self.assertIsNotNone(mx10.runtime_density_profile)
        self.assertEqual(mx10.runtime_density_profile.profile_key, "mx06")

        self.assertIsNotNone(pd01)
        self.assertEqual(pd01.runtime_variant, "pd01")
        self.assertIsNotNone(pd01.runtime_density_profile)
        self.assertEqual(pd01.runtime_density_profile.profile_key, "mx11")

        self.assertIsNotNone(xopoppy)
        self.assertEqual(xopoppy.runtime_variant, "mx10")
        self.assertIsNotNone(xopoppy.runtime_density_profile)
        self.assertEqual(xopoppy.runtime_density_profile.profile_key, "xopoppy")

        self.assertIsNotNone(mx13)
        self.assertEqual(mx13.runtime_variant, "mx10")
        self.assertIsNotNone(mx13.runtime_density_profile)
        self.assertEqual(mx13.runtime_density_profile.profile_key, "xopoppy")

        self.assertIsNotNone(mxw010)
        self.assertEqual(mxw010.runtime_variant, "mx10")
        self.assertIsNone(mxw010.runtime_density_profile)

    def test_device_config_roundtrip_preserves_runtime_fields(self) -> None:
        resolved = self.catalog.detect_device("MX10-ABCD", "AA:BB:CC:DD:EE:59")

        self.assertIsNotNone(resolved)
        config = self.catalog.serialize_device_config(resolved)
        rebuilt = self.catalog.device_from_config(config)

        self.assertEqual(rebuilt.display_name, resolved.display_name)
        self.assertEqual(rebuilt.profile_key, resolved.profile_key)
        self.assertEqual(rebuilt.protocol_family, resolved.protocol_family)
        self.assertEqual(rebuilt.image_pipeline, resolved.image_pipeline)
        self.assertEqual(rebuilt.runtime_variant, resolved.runtime_variant)
        self.assertEqual(
            None if rebuilt.runtime_density_profile is None else rebuilt.runtime_density_profile.profile_key,
            None if resolved.runtime_density_profile is None else resolved.runtime_density_profile.profile_key,
        )
        self.assertEqual(rebuilt.address, resolved.address)
        self.assertEqual(rebuilt.transport_badge, resolved.transport_badge)

    def test_device_config_roundtrip_preserves_protocol_variant(self) -> None:
        resolved = self.catalog.detect_device("QIRUI_Q2_1234")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.protocol_variant, "qirui_q2")
        config = self.catalog.serialize_device_config(resolved)
        rebuilt = self.catalog.device_from_config(config)

        self.assertEqual(rebuilt.profile_key, resolved.profile_key)
        self.assertEqual(rebuilt.protocol_family, resolved.protocol_family)
        self.assertEqual(rebuilt.protocol_variant, resolved.protocol_variant)
        self.assertEqual(rebuilt.image_pipeline, resolved.image_pipeline)

    def test_device_from_config_rejects_unknown_protocol_variant(self) -> None:
        base = self.catalog.device_from_profile("luck_a40")
        config = self.catalog.serialize_device_config(base)
        config["protocol_variant"] = "not_a_variant"

        with self.assertRaisesRegex(
            RuntimeError,
            "luck_normal_a4 does not support protocol variant 'not_a_variant'",
        ):
            self.catalog.device_from_config(config)

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
        self.assertEqual(x6h.protocol_family, ProtocolFamily.LEGACY)

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
        self.assertEqual(x103h.protocol_family, ProtocolFamily.LEGACY)
        self.assertEqual(x2h.profile_key, "x6h")
        self.assertEqual(x2h.protocol_family, ProtocolFamily.LEGACY)

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
                self.assertEqual(resolved.protocol_family, ProtocolFamily.LEGACY)

    def test_tinyprint_spacing_and_alias_corner_cases_still_resolve(self) -> None:
        expected = {
            " X101H-ABCD": ("x101h", ProtocolFamily.LEGACY),
            "X101H-ABCD": ("x101h", ProtocolFamily.LEGACY),
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
        self.assertEqual(resolved.protocol_family, ProtocolFamily.LEGACY)

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
        mx05 = self.catalog.require_profile("mx05")
        mx07 = self.catalog.require_profile("mx07")
        mx10 = self.catalog.require_profile("v5g_small_203")
        xopoppy = self.catalog.require_profile("xopoppy")
        bq02 = self.catalog.require_profile("bq02")
        gt02 = self.catalog.require_profile("gt02_v5g")
        shared = self.catalog.require_profile("v5g_small_203")

        self.assertEqual(mx05.default_protocol_family, ProtocolFamily.V5G)
        self.assertEqual(mx05.default_image_pipeline.encoding, ImageEncoding.V5G_DOT)
        self.assertEqual(mx05.default_image_pipeline.formats[0], PixelFormat.BW1)
        self.assertIsNotNone(mx05.density)
        self.assertEqual(mx05.energy.text.high, 20000)

        self.assertIsNotNone(mx07.density)
        self.assertEqual(mx07.density.image.high, 100)

        self.assertEqual(mx10.default_protocol_family, ProtocolFamily.V5G)
        self.assertEqual(mx10.speed.image, 30)
        self.assertEqual(mx10.energy.image.middle, 10000)
        self.assertEqual(mx10.energy.text.high, 20000)

        self.assertIsNotNone(xopoppy.density)
        self.assertEqual(xopoppy.density.text.middle, 80)

        self.assertIsNotNone(bq02.density)
        self.assertEqual(bq02.density.text.high, 180)

        self.assertIsNotNone(gt02.density)
        self.assertEqual(gt02.density.image.middle, 110)
        self.assertEqual(gt02.density.text.high, 150)

        self.assertEqual(shared.default_protocol_family, ProtocolFamily.V5G)
        self.assertEqual(shared.speed.image, 30)
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
            "MXTP-100-ABCD": "mx06",
            "MXPC-100-ABCD": "v5g_small_203",
            "LY10-ABCD": "ly10",
            "PD01-ABCD": "v5g_small_203",
            "AZ-P2108X-ABCD": "v5g_small_203",
            "MX12-ABCD": "v5g_small_203",
            "MX13-ABCD": "v5g_small_203",
            "MX07-ABCD": "mx07",
            "XOPOPPY-ABCD": "xopoppy",
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
            "CYLOBTPrinter": "mx06",
            "EWTTO ET-Z0499": "mx06",
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
            "T02-ABCD",
            "T02E-ABCD",
            "Q02E",
            "C02E",
            "MXW-A4-ABCD",
        ):
            with self.subTest(name=name):
                self.assertIsNone(self.catalog.detect_device(name))


if __name__ == "__main__":
    unittest.main()
