from __future__ import annotations

import unittest
from pathlib import Path

from tools.render_readme_models import assert_readme_is_current

REPO_ROOT = Path(__file__).resolve().parent.parent


class DocsSanityTests(unittest.TestCase):
    def test_protocol_and_architecture_docs_do_not_use_removed_api_names(self) -> None:
        paths = [
            REPO_ROOT / "docs/protocol.md",
            REPO_ROOT / "docs/architecture.md",
            REPO_ROOT / "README.md",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        forbidden = [
            "PrinterModelRegistry",
            "from timiniprint.protocol import Raster",
            "from timiniprint.protocol import PixelFormat",
            "from timiniprint.protocol import RasterBuffer",
            "from timiniprint.protocol import RasterSet",
            "from timiniprint.protocol.types import PixelFormat",
            "from timiniprint.protocol.types import RasterBuffer",
            "from timiniprint.protocol.types import RasterSet",
            "PreparedPrintJob",
            "BluetoothPrinterResolver",
            "BluetoothPrinterDiscovery",
            "SerialTransport",
            "from timiniprint.protocol import build_job_from_raster",
            "from timiniprint.protocol import build_job_from_raster_set",
            "compress=True",
            "new_format=False",
        ]
        for pattern in forbidden:
            self.assertNotIn(pattern, text)

    def test_readme_model_sections_are_generated_from_catalog(self) -> None:
        assert_readme_is_current()


if __name__ == "__main__":
    unittest.main()
