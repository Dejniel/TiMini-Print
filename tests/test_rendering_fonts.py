from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from timiniprint.rendering import fonts


class RenderingFontsTests(unittest.TestCase):
    def test_has_executable_checks_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tool")
            with open(path, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
            os.chmod(path, 0o755)
            with patch.dict(os.environ, {"PATH": tmp}):
                self.assertTrue(fonts._has_executable("tool"))
                self.assertFalse(fonts._has_executable("missing"))

    def test_find_fc_match_and_fallback(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            fake_font = tmp.name
        try:
            result = Mock(stdout=fake_font + "\n")
            with patch("timiniprint.rendering.fonts._has_executable", return_value=True), patch(
                "subprocess.run", return_value=result
            ):
                self.assertEqual(fonts._find_fc_match(), fake_font)
        finally:
            os.remove(fake_font)

    def test_find_common_monospace_first_existing(self) -> None:
        with patch("os.path.isfile", side_effect=lambda p: p.endswith("DejaVuSansMono-Bold.ttf")):
            found = fonts._find_common_monospace()
        self.assertTrue(found.endswith("DejaVuSansMono-Bold.ttf"))

    def test_find_monospace_bold_font_prefers_fc_match(self) -> None:
        with patch("timiniprint.rendering.fonts._find_fc_match", return_value="/a.ttf"), patch(
            "timiniprint.rendering.fonts._find_common_monospace", return_value="/b.ttf"
        ):
            self.assertEqual(fonts.find_monospace_bold_font(), "/a.ttf")

    def test_load_font_fallback(self) -> None:
        font = fonts.load_font(None, 12)
        self.assertIsNotNone(font)


if __name__ == "__main__":
    unittest.main()
