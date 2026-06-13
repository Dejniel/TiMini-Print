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
            if error["kind"] in {"duplicate_profile_body", "duplicate_rule_body"}
        ]
        duplicate_warnings = [
            warning
            for warning in report["warnings"]
            if warning["kind"] in {"duplicate_profile_body", "duplicate_rule_body"}
        ]

        self.assertEqual(duplicate_errors, [])
        self.assertEqual(duplicate_warnings, [])

    def test_catalog_audit_detects_shadowed_rule(self) -> None:
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
                "default_protocol_family": "tiny",
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
                "profile_key": "specific",
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
                "default_protocol_family": "tiny",
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
        rules = [
            {
                "rule_key": "generic",
                "prefixes": ["FOO"],
                "profile_key": "base",
                "protocol_family": "tiny",
            },
            {
                "rule_key": "specific",
                "prefixes": ["FOOBAR"],
                "profile_key": "specific",
                "protocol_family": "tiny",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profiles.json"
            rule_path = Path(tmp) / "rules.json"
            profile_path.write_text(json.dumps(profiles), encoding="utf-8")
            rule_path.write_text(json.dumps(rules), encoding="utf-8")

            report = self.tool.generate_report(profile_path=profile_path, rule_path=rule_path)

        shadowed = [error for error in report["errors"] if error["kind"] == "shadowed_rule"]
        self.assertEqual(len(shadowed), 1)
        self.assertEqual(shadowed[0]["rule_key"], "specific")


if __name__ == "__main__":
    unittest.main()
