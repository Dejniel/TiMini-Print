from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from timiniprint import licensing


class LicensingTests(unittest.TestCase):
    def test_bundled_license_text_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "THIRD_PARTY_LICENSES.txt"
            bundled.write_text("bundled licenses\n", encoding="utf-8")
            with patch.object(licensing, "_BUNDLED_LICENSES", bundled):
                self.assertEqual(licensing.license_text(), "bundled licenses\n")

    def test_source_fallback_contains_project_license_and_notices(self) -> None:
        with patch.object(licensing, "_BUNDLED_LICENSES", Path("/missing")):
            text = licensing.license_text()
        self.assertIn("Apache License", text)
        self.assertIn("Daniel Banecki", text)
        self.assertIn("pypdfium2", text)


if __name__ == "__main__":
    unittest.main()
