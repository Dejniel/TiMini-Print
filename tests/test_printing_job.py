from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tests.helpers import install_crc8_stub, reset_registry_cache
from timiniprint.devices.models import PrinterModelRegistry
from timiniprint.rendering.converters.base import Page


class _FakeLoader:
    supported_extensions = {".txt", ".png"}

    def __init__(self, pages):
        self._pages = pages

    def load(self, path: str, width: int):
        _ = path, width
        return list(self._pages)


class PrintingJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.job_mod = importlib.import_module("timiniprint.printing.job")

    def setUp(self) -> None:
        reset_registry_cache()
        self.model = PrinterModelRegistry.load().get("X6H") or PrinterModelRegistry.load().models[0]

    def test_static_helpers(self) -> None:
        self.assertEqual(self.job_mod.PrintJobBuilder._normalized_width(384), 384)
        self.assertEqual(self.job_mod.PrintJobBuilder._normalized_width(386), 384)
        self.assertEqual(self.job_mod.PrintJobBuilder._mm_to_px(0, 203), 0)
        self.assertGreater(self.job_mod.PrintJobBuilder._mm_to_px(5, 203), 0)

    def test_build_from_file_validation(self) -> None:
        builder = self.job_mod.PrintJobBuilder(self.model, page_loader=_FakeLoader([]))
        with self.assertRaises(ValueError):
            builder.build_from_file("bad.unsupported")
        with self.assertRaises(FileNotFoundError):
            builder.build_from_file("missing.txt")

    def test_build_from_file_uses_loader_renderer_and_build_job(self) -> None:
        img = Image.new("1", (8, 1), 1)
        pages = [Page(img, dither=False, is_text=False), Page(img, dither=True, is_text=True)]
        loader = _FakeLoader(pages)
        builder = self.job_mod.PrintJobBuilder(self.model, page_loader=loader)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_text("x", encoding="utf-8")
            with patch("timiniprint.printing.job.image_to_bw_pixels", return_value=[1] * 8), patch(
                "timiniprint.printing.job.build_job", side_effect=[b"A", b"B"]
            ):
                out = builder.build_from_file(str(path))
        self.assertEqual(out, b"AB")

    def test_mode_energy_speed_selection(self) -> None:
        settings = self.job_mod.PrintSettings(text_mode=None, compress=None, lsb_first=None)
        builder = self.job_mod.PrintJobBuilder(self.model, settings=settings, page_loader=_FakeLoader([]))
        p_text = Page(Image.new("1", (8, 1), 1), dither=False, is_text=True)
        p_img = Page(Image.new("1", (8, 1), 1), dither=True, is_text=False)
        self.assertTrue(builder._select_text_mode(p_text))
        self.assertFalse(builder._select_text_mode(p_img))
        self.assertGreater(builder._select_energy(True), 0)
        self.assertGreater(builder._select_energy(False), 0)


if __name__ == "__main__":
    unittest.main()
