from __future__ import annotations

import unittest
from pathlib import Path

from tools.package_release import _is_license_file, _static_license_directory


class PackageReleaseTests(unittest.TestCase):
    def test_distribution_license_directories_are_included(self) -> None:
        self.assertTrue(
            _is_license_file(
                Path("pypdfium2-5.11.0.dist-info/licenses/data/BUILD_LICENSES/pdfium.txt")
            )
        )

    def test_named_license_files_are_included(self) -> None:
        self.assertTrue(_is_license_file(Path("package/LICENSE.txt")))
        self.assertTrue(_is_license_file(Path("package/NOTICE")))

    def test_source_modules_named_licenses_are_not_included(self) -> None:
        self.assertFalse(_is_license_file(Path("packaging/licenses/_spdx.py")))

    def test_platform_package_families_share_official_fallbacks(self) -> None:
        root = Path("licenses")
        self.assertEqual(
            _static_license_directory(root, "pyobjc-framework-iobluetooth"),
            root / "pyobjc",
        )
        self.assertEqual(
            _static_license_directory(root, "winrt-runtime"),
            root / "winrt",
        )
        self.assertEqual(
            _static_license_directory(root, "winsdk"),
            root / "winsdk",
        )


if __name__ == "__main__":
    unittest.main()
