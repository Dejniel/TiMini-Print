from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "tools/catalog_audit.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("catalog_audit", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load catalog_audit module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CatalogAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = _load_module()

    def test_catalog_audit_has_no_errors(self) -> None:
        report = self.tool.generate_report()

        self.assertEqual(report["errors"], [])

    def test_catalog_audit_has_no_duplicate_findings(self) -> None:
        report = self.tool.generate_report()
        duplicate_errors = [
            error
            for error in report["errors"]
            if error["kind"] in {"duplicate_profile_body", "duplicate_model_body", "mergeable_model_group"}
        ]
        duplicate_warnings = [
            warning
            for warning in report["warnings"]
            if warning["kind"] in {"duplicate_profile_body", "duplicate_model_body", "mergeable_model_group"}
        ]

        self.assertEqual(duplicate_errors, [])
        self.assertEqual(duplicate_warnings, [])

    def test_catalog_audit_detects_shadowed_model(self) -> None:
        profiles = [
            {
                "profile_key": "base",
                "size": 1,
                "paper_size": 1,
                "print_size": 384,
                "one_length": 8,
                "dev_dpi": 203,
                "ble_mtu_request": 23,
                "has_id": False,
                "use_spp": False,
                "can_print_label": False,
                "label_value": "",
                "back_paper_num": 0,
                "protocol_default": {"type": "tiny"},
                "default_image_pipeline": {"formats": ["bw1"], "encoding": "tiny_raw"},
                "stream": {"chunk_size": 180, "delay_ms": 4},
                "post_print_feed_count": 2,
                "print_defaults": {
                    "speed": {"image": 10, "text": 8},
                    "energy": {
                        "image": {"low": 5000, "middle": 5000, "high": 5000},
                        "text": {"low": 8000, "middle": 8000, "high": 8000},
                    },
                },
            },
            {
                "profile_key": "specific_profile",
                "size": 1,
                "paper_size": 1,
                "print_size": 384,
                "one_length": 8,
                "dev_dpi": 203,
                "ble_mtu_request": 23,
                "has_id": False,
                "use_spp": False,
                "can_print_label": False,
                "label_value": "",
                "back_paper_num": 0,
                "protocol_default": {"type": "tiny"},
                "default_image_pipeline": {"formats": ["bw1"], "encoding": "tiny_raw"},
                "stream": {"chunk_size": 180, "delay_ms": 4},
                "post_print_feed_count": 2,
                "print_defaults": {
                    "speed": {"image": 10, "text": 8},
                    "energy": {
                        "image": {"low": 5000, "middle": 5000, "high": 5000},
                        "text": {"low": 8000, "middle": 8000, "high": 8000},
                    },
                },
            },
        ]
        models = [
            {
                "model_key": "generic",
                "detections": [{"name": "FOO", "detection": {"prefixes": ["FOO"]}}],
                "profile_key": "base",
                "protocol_override": {"type": "tiny"},
                "origin_app_packages": ["com.example.generic"],
            },
            {
                "model_key": "specific",
                "detections": [{"name": "FOO", "detection": {"prefixes": ["FOO"]}}],
                "profile_key": "specific_profile",
                "protocol_override": {"type": "tiny"},
                "origin_app_packages": ["com.example.specific"],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profiles.json"
            model_path = Path(tmp) / "models.json"
            profile_path.write_text(json.dumps(profiles), encoding="utf-8")
            model_path.write_text(json.dumps(models), encoding="utf-8")

            report = self.tool.generate_report(profile_path=profile_path, model_path=model_path)

        shadowed = [error for error in report["errors"] if error["kind"] == "shadowed_model"]
        self.assertEqual(len(shadowed), 1)
        self.assertEqual(shadowed[0]["model_key"], "specific")

    def test_catalog_audit_detects_mergeable_model_group(self) -> None:
        profiles = [
            {
                "profile_key": "base",
                "size": 1,
                "paper_size": 1,
                "print_size": 384,
                "one_length": 8,
                "dev_dpi": 203,
                "ble_mtu_request": 23,
                "has_id": False,
                "use_spp": False,
                "can_print_label": False,
                "label_value": "",
                "back_paper_num": 0,
                "protocol_default": {"type": "tiny"},
                "default_image_pipeline": {"formats": ["bw1"], "encoding": "tiny_raw"},
                "stream": {"chunk_size": 180, "delay_ms": 4},
                "post_print_feed_count": 2,
                "print_defaults": {
                    "speed": {"image": 10, "text": 8},
                    "energy": {
                        "image": {"low": 5000, "middle": 5000, "high": 5000},
                        "text": {"low": 8000, "middle": 8000, "high": 8000},
                    },
                },
            },
        ]
        models = [
            {
                "model_key": "first",
                "detections": [{"name": "FOO", "detection": {"prefixes": ["FOO"]}}],
                "profile_key": "base",
                "protocol_override": {"type": "tiny"},
                "origin_app_packages": ["com.example.source"],
            },
            {
                "model_key": "second",
                "detections": [{"name": "BAR", "detection": {"prefixes": ["BAR"]}}],
                "profile_key": "base",
                "protocol_override": {"type": "tiny"},
                "origin_app_packages": ["com.example.other_source"],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profiles.json"
            model_path = Path(tmp) / "models.json"
            profile_path.write_text(json.dumps(profiles), encoding="utf-8")
            model_path.write_text(json.dumps(models), encoding="utf-8")

            report = self.tool.generate_report(profile_path=profile_path, model_path=model_path)

        errors = [error for error in report["errors"] if error["kind"] == "mergeable_model_group"]
        self.assertEqual(errors, [{"kind": "mergeable_model_group", "model_keys": ["first", "second"]}])

    def test_catalog_audit_detects_unsupported_model_that_is_already_supported(self) -> None:
        profiles = [
            {
                "profile_key": "base",
                "size": 1,
                "paper_size": 1,
                "print_size": 384,
                "one_length": 8,
                "dev_dpi": 203,
                "ble_mtu_request": 23,
                "has_id": False,
                "use_spp": False,
                "can_print_label": False,
                "label_value": "",
                "back_paper_num": 0,
                "protocol_default": {"type": "tiny"},
                "default_image_pipeline": {"formats": ["bw1"], "encoding": "tiny_raw"},
                "stream": {"chunk_size": 180, "delay_ms": 4},
                "post_print_feed_count": 2,
                "print_defaults": {
                    "speed": {"image": 10, "text": 8},
                    "energy": {
                        "image": {"low": 5000, "middle": 5000, "high": 5000},
                        "text": {"low": 8000, "middle": 8000, "high": 8000},
                    },
                },
            },
        ]
        models = [
            {
                "model_key": "supported",
                "detections": [{"name": "FOO", "detection": {"prefixes": ["FOO"]}}],
                "profile_key": "base",
                "protocol_override": {"type": "tiny"},
                "origin_app_packages": ["com.example.supported"],
            },
        ]
        unsupported_models = [
            {
                "model_key": "unsupported",
                "detections": [{"name": "FOO", "detection": {"prefixes": ["FOO"]}}],
                "origin_app_packages": [],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profiles.json"
            model_path = Path(tmp) / "models.json"
            unsupported_model_path = Path(tmp) / "unsupported.json"
            profile_path.write_text(json.dumps(profiles), encoding="utf-8")
            model_path.write_text(json.dumps(models), encoding="utf-8")
            unsupported_model_path.write_text(json.dumps(unsupported_models), encoding="utf-8")

            report = self.tool.generate_report(
                profile_path=profile_path,
                model_path=model_path,
                unsupported_model_path=unsupported_model_path,
            )

        errors = [
            error
            for error in report["errors"]
            if error["kind"] == "unsupported_model_matches_supported_model"
        ]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["model_key"], "unsupported")


if __name__ == "__main__":
    unittest.main()
