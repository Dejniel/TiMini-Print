from __future__ import annotations

import re
import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol.families import get_protocol_behavior
from tools.render_readme_models import (
    load_inventory_entries,
    render_supported_models_block,
    render_todo_models_block,
    validate_inventory,
)


def _detect_readme_name(catalog: PrinterCatalog, name: str):
    candidates = (
        name,
        f"{name}-ABCD",
        f"{name}_ABCD",
        f"{name}ABCD",
    )
    for candidate in candidates:
        detected = catalog.detect_device(candidate)
        if detected is not None:
            return detected
    return None


class ReadmeModelInventoryTests(unittest.TestCase):
    def test_inventory_validates_against_runtime_catalog(self) -> None:
        entries = load_inventory_entries()

        self.assertEqual(validate_inventory(entries), [])

    def test_readme_inventory_matches_runtime_detection_contract(self) -> None:
        catalog = PrinterCatalog.load()
        entries = load_inventory_entries()

        for entry in entries:
            for name in entry.visible_names:
                with self.subTest(entry=entry.id, status=entry.status, name=name):
                    detected = _detect_readme_name(catalog, name)
                    if entry.status == "supported":
                        self.assertIsNotNone(detected)
                        assert detected is not None
                        self.assertTrue(
                            get_protocol_behavior(detected.protocol_family).implemented,
                            detected.protocol_family,
                        )
                    elif entry.rule_keys:
                        if detected is not None:
                            self.assertIn(detected.detection_rule_key, entry.rule_keys)
                    else:
                        self.assertIsNone(detected)

    def test_supported_and_todo_blocks_render_non_empty_content(self) -> None:
        entries = load_inventory_entries()

        supported = render_supported_models_block(entries)
        todo = render_todo_models_block(entries)

        self.assertIn("CTP750BY (Shipping Printer)", supported)
        self.assertRegex(supported, r"(?<![A-Z0-9_-])APA46Y(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])PPA2L(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])PPA2LH(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])DP_A4(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])DL_X7Pro(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])P4(?![A-Z0-9_-])")
        self.assertIn("P1 (TSPL)", supported)
        self.assertIn("P1 (legacy)", supported)
        self.assertIn("- P11 (HPRT ESC) and clones: P2_, P3_, P5_, YHK_", supported)
        self.assertIn("- JXPRINTER and clones: PRINTER", todo)
        self.assertIn("- BAYPAGE and clones: YINTIBAO-V8S", todo)
        self.assertIn("- P100 and clones: MP100, MP200, MP220, YINTIBAO-V5, AEQ918N4", todo)
        self.assertIn("- P100S and clones: MP100S, MP200S, MP220S, YINTIBAO-V5PRO", todo)
        self.assertIn("MP300", todo)
        self.assertIn("- P3S and clones: MP300S", todo)
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])DL_X7Pro(?![A-Z0-9_-])", todo))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P4(?![A-Z0-9_-])", todo))
        self.assertRegex(todo, r"(?<![A-Z0-9_-])MXW-A4(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])JX400R06P(?![A-Z0-9_-])")
        self.assertNotIn("(iBleem", todo)
        self.assertNotIn("(Luck ", todo)
        self.assertIsNone(re.search(r"^- P4(?:\s|$)", todo, re.M))
        self.assertIsNone(re.search(r"^- MXW-A4(?:\s|$)", todo, re.M))
        self.assertIsNone(re.search(r"^- JX400R06P(?:\s|$)", todo, re.M))
        todo_flat_line = todo.splitlines()[0]
        self.assertIn("JX400R", todo_flat_line)
        self.assertIn("JX400R06P", todo_flat_line)
        self.assertIn("MXW-A4", todo_flat_line)
        self.assertIn("D11", todo_flat_line)
        self.assertIn("D110_M", todo_flat_line)
        self.assertIn("B21", todo_flat_line)
        self.assertIsNone(re.search(r"^- APA46Y(?:\s|$)", supported, re.M))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P100(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P100S(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])MP100(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])MP100S(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])LP100(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])LP100S(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P3S(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])M08F(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])M832(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])Q302(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])T02(?![A-Z0-9_-])", supported))
        self.assertIn("- M08F and clones: TP81, TP84, TP85, TP86, TP87, TP88", todo)
        self.assertIn("- M832 and clones: M836", todo)
        self.assertIn("- Q302 and clones: Q580", todo)
        self.assertIn("- T02 and clones: T02E, Q02E, C02E", todo)
        self.assertIsNone(re.search(r"/", supported))
        self.assertIsNone(re.search(r"/", todo))
        self.assertNotIn("DP_A4 and clones: DP-A4", supported)

    def test_rendered_readme_names_do_not_keep_unexpected_detection_suffixes(self) -> None:
        entries = load_inventory_entries()

        rendered = "\n".join(
            [
                render_supported_models_block(entries),
                render_todo_models_block(entries),
            ]
        )
        rendered = re.sub(
            r"^- P11 \(HPRT ESC\) and clones: P2_, P3_, P5_, YHK_$",
            "- P11 (HPRT ESC) and clones: P2, P3, P5, YHK",
            rendered,
            flags=re.M,
        )

        self.assertIsNone(re.search(r"\b[^\s,()]+[_-](?=[,\n])", rendered))


if __name__ == "__main__":
    unittest.main()
