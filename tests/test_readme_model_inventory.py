from __future__ import annotations

import re
import unittest

from tools.render_readme_models import (
    load_inventory_entries,
    render_supported_models_block,
    render_todo_models_block,
    validate_inventory,
)


class ReadmeModelInventoryTests(unittest.TestCase):
    def test_inventory_validates_against_runtime_catalog(self) -> None:
        entries = load_inventory_entries()

        self.assertEqual(validate_inventory(entries), [])

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
        self.assertIn("- JXPRINTER and clones: PRINTER", todo)
        self.assertIn("- BAYPAGE and clones: YINTIBAO-V8S", todo)
        self.assertIn("- P100 and clones: MP100, MP200, MP220, YINTIBAO-V5, AEQ918N4", todo)
        self.assertIn("- P100S and clones: MP100S, MP200S, MP220S, YINTIBAO-V5PRO", todo)
        self.assertIn("- P3 and clones: MP300", todo)
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
        self.assertTrue(todo.splitlines()[0].startswith("JX400R, JX400R06P, MXW-A4"))
        self.assertIsNone(re.search(r"^- APA46Y(?:\s|$)", supported, re.M))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P100(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P100S(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])MP100(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])MP100S(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])LP100(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])LP100S(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P3(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P3S(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"/", supported))
        self.assertIsNone(re.search(r"/", todo))
        self.assertNotIn("DP_A4 and clones: DP-A4", supported)

    def test_rendered_readme_names_do_not_keep_trailing_detection_suffixes(self) -> None:
        entries = load_inventory_entries()

        rendered = "\n".join(
            [
                render_supported_models_block(entries),
                render_todo_models_block(entries),
            ]
        )

        self.assertIsNone(re.search(r"\b[^\s,()]+[_-](?=[,\n])", rendered))


if __name__ == "__main__":
    unittest.main()
