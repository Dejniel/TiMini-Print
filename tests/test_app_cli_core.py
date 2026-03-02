from __future__ import annotations

import argparse
import unittest

from timiniprint.app import cli


class AppCliCoreTests(unittest.TestCase):
    def test_resolve_helpers(self) -> None:
        args = argparse.Namespace(
            force_text_mode=True,
            force_image_mode=False,
            darkness=4,
            text="abc",
            text_font="/tmp/f.ttf",
            text_columns=12,
            text_hard_wrap=False,
            pdf_pages="1,2",
            pdf_page_gap=3,
            trim_side_margins=True,
            trim_top_bottom_margins=False,
            feed=False,
            retract=True,
        )
        self.assertTrue(cli._resolve_text_mode(args))
        self.assertEqual(cli._resolve_blackening(args), 4)
        self.assertEqual(cli._resolve_text_input(args), "abc")
        self.assertEqual(cli._resolve_text_font(args), "/tmp/f.ttf")
        self.assertEqual(cli._resolve_text_columns(args), 12)
        self.assertTrue(cli._resolve_text_wrap(args))
        self.assertEqual(cli._resolve_pdf_pages(args), "1,2")
        self.assertEqual(cli._resolve_pdf_page_gap(args), 3)
        self.assertTrue(cli._resolve_trim_side_margins(args))
        self.assertFalse(cli._resolve_trim_top_bottom_margins(args))
        self.assertEqual(cli._resolve_paper_motion_action(args), "retract")

    def test_text_columns_validation(self) -> None:
        args = argparse.Namespace(text_columns=0)
        with self.assertRaises(ValueError):
            cli._resolve_text_columns(args)

    def test_build_cli_reporter_verbose(self) -> None:
        r1 = cli._build_cli_reporter(verbose=False)
        r2 = cli._build_cli_reporter(verbose=True)
        sink1 = r1._sinks[0]
        sink2 = r2._sinks[0]
        self.assertNotIn("debug", sink1._levels)
        self.assertIn("debug", sink2._levels)


if __name__ == "__main__":
    unittest.main()
