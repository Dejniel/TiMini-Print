from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "timiniprint"


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _module_name_for_path(path: Path) -> str:
    return ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)


def _resolve_from_target(package_parts: list[str], level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    trim = max(level - 1, 0)
    if trim > len(package_parts):
        base_parts: list[str] = []
    else:
        base_parts = package_parts[: len(package_parts) - trim]
    if not module:
        return ".".join(base_parts)
    return ".".join(base_parts + module.split("."))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = _module_name_for_path(path).split(".")[:-1]
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        target = _resolve_from_target(package_parts, node.level, node.module)
        if target:
            imports.add(target)
        if not node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                if target:
                    imports.add(f"{target}.{alias.name}")
                else:
                    imports.add(alias.name)
    return imports


class ArchitectureBoundaryTests(unittest.TestCase):
    def _assert_no_forbidden_imports(self, root: Path, forbidden_prefixes: list[str]) -> None:
        violations: list[str] = []
        for path in _iter_python_files(root):
            imported = sorted(
                value
                for value in _imported_modules(path)
                if any(
                    value == prefix or value.startswith(prefix + ".")
                    for prefix in forbidden_prefixes
                )
            )
            if imported:
                violations.append(f"{path.relative_to(REPO_ROOT)}: {imported}")
        self.assertEqual(violations, [])

    def test_rendering_does_not_import_protocol_package(self) -> None:
        self._assert_no_forbidden_imports(
            PACKAGE_ROOT / "rendering",
            ["timiniprint.protocol"],
        )

    def test_rendering_does_not_import_printing_or_transport_layers(self) -> None:
        self._assert_no_forbidden_imports(
            PACKAGE_ROOT / "rendering",
            [
                "timiniprint.printing",
                "timiniprint.transport",
                "timiniprint.app",
            ],
        )

    def test_protocol_stays_independent_from_higher_layers(self) -> None:
        self._assert_no_forbidden_imports(
            PACKAGE_ROOT / "protocol",
            [
                "timiniprint.rendering",
                "timiniprint.printing",
                "timiniprint.transport",
                "timiniprint.app",
            ],
        )

    def test_protocol_uses_only_device_model(self) -> None:
        violations: list[str] = []
        allowed_prefixes = {"timiniprint.devices.device"}
        for path in _iter_python_files(PACKAGE_ROOT / "protocol"):
            imported = sorted(
                value
                for value in _imported_modules(path)
                if (
                    value == "timiniprint.devices"
                    or value.startswith("timiniprint.devices.")
                )
                and not any(
                    value == prefix or value.startswith(prefix + ".")
                    for prefix in allowed_prefixes
                )
            )
            if imported:
                violations.append(f"{path.relative_to(REPO_ROOT)}: {imported}")
        self.assertEqual(violations, [])

    def test_devices_do_not_import_transport(self) -> None:
        self._assert_no_forbidden_imports(
            PACKAGE_ROOT / "devices",
            ["timiniprint.transport"],
        )

    def test_transport_does_not_import_printing_layer(self) -> None:
        self._assert_no_forbidden_imports(
            PACKAGE_ROOT / "transport",
            ["timiniprint.printing"],
        )

    def test_transport_does_not_import_family_implementations(self) -> None:
        self._assert_no_forbidden_imports(
            PACKAGE_ROOT / "transport",
            [
                "timiniprint.printing.runtime.v5g",
                "timiniprint.printing.runtime.v5x",
                "timiniprint.printing.runtime.v5c",
                "timiniprint.protocol.families.v5g",
                "timiniprint.protocol.families.v5x",
                "timiniprint.protocol.families.v5c",
            ],
        )


if __name__ == "__main__":
    unittest.main()
