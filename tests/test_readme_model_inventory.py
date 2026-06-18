from __future__ import annotations

import re
import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import SupportedModelMatch, UnsupportedModelMatch
from tools.render_readme_models import (
    render_supported_models_block,
    render_todo_models_block,
    validate_catalog_models,
)


def _readme_name_candidates(name: str) -> tuple[str, ...]:
    return (
        name,
        f"{name}-ABCD",
        f"{name}_ABCD",
        f"{name}ABCD",
    )


def _detect_supported_readme_name(catalog: PrinterCatalog, name: str):
    for candidate in _readme_name_candidates(name):
        detected = catalog.detect_device(candidate)
        if detected is not None:
            return detected
    return None


def _supported_readme_name_matches_model(
    catalog: PrinterCatalog,
    name: str,
    model_key: str,
) -> bool:
    detected = _detect_supported_readme_name(catalog, name)
    if detected is not None and detected.model_key == model_key:
        return True
    return model_key in {
        model.model_key
        for model in catalog.get_models_by_detection_name(name)
    }


def _unsupported_detection_matches_model(
    catalog: PrinterCatalog,
    model,
    trigger: str,
    *,
    is_prefix: bool,
) -> bool:
    candidates = (f"{trigger}ABCD", trigger) if is_prefix else (trigger,)
    model_origins = set(model.origin_app_packages)
    for candidate in candidates:
        for match in catalog.detect_model(candidate):
            if isinstance(match, UnsupportedModelMatch) and match.model.model_key == model.model_key:
                return True
            if (
                isinstance(match, SupportedModelMatch)
                and model_origins
                and model_origins.isdisjoint(match.model.origin_app_packages)
            ):
                return True
    return False


class ReadmeModelInventoryTests(unittest.TestCase):
    def test_catalog_models_validate_for_readme_rendering(self) -> None:
        self.assertEqual(validate_catalog_models(), [])

    def test_readme_names_match_catalog_detection_contract(self) -> None:
        catalog = PrinterCatalog.load()

        for model in catalog.models:
            for name in model.names:
                with self.subTest(model=model.model_key, status="supported", name=name):
                    self.assertTrue(
                        _supported_readme_name_matches_model(catalog, name, model.model_key)
                    )

        for model in catalog.unsupported_models:
            for detection in model.detections:
                for prefix in detection.detection.prefixes:
                    with self.subTest(model=model.model_key, status="unsupported", prefix=prefix):
                        self.assertTrue(
                            _unsupported_detection_matches_model(
                                catalog, model, prefix, is_prefix=True
                            )
                        )
                for exact_name in detection.detection.exact_names:
                    with self.subTest(model=model.model_key, status="unsupported", exact=exact_name):
                        self.assertTrue(
                            _unsupported_detection_matches_model(
                                catalog, model, exact_name, is_prefix=False
                            )
                        )

    def test_supported_and_todo_blocks_render_non_empty_content(self) -> None:
        supported = render_supported_models_block()
        todo = render_todo_models_block()

        self.assertRegex(supported, r"(?<![A-Z0-9_-])APA46Y(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])PPA2L(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])PPA2LH(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])DP_A4(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])DL_X7Pro(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])P4(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])P1(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])P11(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])P2(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])M02(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])M02S(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])M110(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])M120(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])T02(?![A-Z0-9_-])")
        self.assertRegex(supported, r"(?<![A-Z0-9_-])Q02E(?![A-Z0-9_-])")

        self.assertRegex(todo, r"(?<![A-Z0-9_-])JXPRINTER(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])PRINTER(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])BAYPAGE(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])YINTIBAO-V8S(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])P100(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])MP100(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])MP300(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])P3S(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])MXW-A4(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])JX400R06P(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])D11(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])D61(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])Betty(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])S6_P(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])D110_M(?![A-Z0-9_-])")
        self.assertRegex(todo, r"(?<![A-Z0-9_-])B21(?![A-Z0-9_-])")

        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])DL_X7Pro(?![A-Z0-9_-])", todo))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P4(?![A-Z0-9_-])", todo))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P100(?![A-Z0-9_-])", supported))
        self.assertIsNone(re.search(r"(?<![A-Z0-9_-])P100S(?![A-Z0-9_-])", supported))


if __name__ == "__main__":
    unittest.main()
