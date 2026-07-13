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

    def test_source_run_builds_license_text_on_demand(self) -> None:
        with patch.object(licensing, "_BUNDLED_LICENSES", Path("/missing")), patch.object(
            licensing,
            "build_license_text",
            return_value="generated licenses\n",
        ) as build:
            text = licensing.license_text()

        self.assertEqual(text, "generated licenses\n")
        build.assert_called_once_with(strict=False)

    def test_generated_manifest_precedes_project_license(self) -> None:
        with patch.object(licensing, "_resolve_runtime_distributions", return_value=[]):
            text = licensing.build_license_text()

        self.assertLess(text.index("Installed component manifest"), text.index("Apache License"))

    def test_source_run_skips_missing_optional_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            requirements = Path(tmp) / "requirements.txt"
            requirements.write_text("missing-pdf-renderer>=1\n", encoding="utf-8")
            with patch.object(
                licensing.metadata,
                "distribution",
                side_effect=licensing.metadata.PackageNotFoundError("missing-pdf-renderer"),
            ):
                distributions = licensing._resolve_runtime_distributions(
                    requirements,
                    strict=False,
                )

        self.assertEqual(distributions, [])

    def test_release_build_rejects_missing_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            requirements = Path(tmp) / "requirements.txt"
            requirements.write_text("missing-runtime>=1\n", encoding="utf-8")
            with patch.object(
                licensing.metadata,
                "distribution",
                side_effect=licensing.metadata.PackageNotFoundError("missing-runtime"),
            ):
                with self.assertRaises(licensing.metadata.PackageNotFoundError):
                    licensing._resolve_runtime_distributions(requirements, strict=True)


if __name__ == "__main__":
    unittest.main()
