from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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


def _paper_preset_type_imports(tree: ast.AST, package_parts: list[str]) -> set[ast.ImportFrom]:
    allowed = set()
    for guard in ast.walk(tree):
        if not isinstance(guard, ast.If):
            continue
        condition = guard.test
        if not (
            isinstance(condition, ast.Name) and condition.id == "TYPE_CHECKING"
            or isinstance(condition, ast.Attribute) and condition.attr == "TYPE_CHECKING"
            and isinstance(condition.value, ast.Name) and condition.value.id == "typing"
        ):
            continue
        for statement in guard.body:
            for node in ast.walk(statement):
                if (isinstance(node, ast.ImportFrom)
                        and _resolve_from_target(package_parts, node.level, node.module) == "timiniprint.devices.profiles"
                        and all(alias.name == "PaperPreset" for alias in node.names)):
                    allowed.add(node)
    return allowed


def _imported_modules(path: Path, *, allow_paper_preset_typing: bool = False) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = _module_name_for_path(path).split(".")[:-1]
    allowed = _paper_preset_type_imports(tree, package_parts) if allow_paper_preset_typing else set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if node in allowed:
            continue
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
                for value in _imported_modules(path, allow_paper_preset_typing=True)
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

    def test_paper_preset_annotation_import_is_narrow(self) -> None:
        profiles = "timiniprint.devices.profiles"
        cases = [
            ("if TYPE_CHECKING:\n    from ..devices.profiles import PaperPreset", set()),
            ("if typing.TYPE_CHECKING:\n    from timiniprint.devices.profiles import PaperPreset as Preset", set()),
            ("from ..devices.profiles import PaperPreset", {profiles}),
            ("if debug:\n    from ..devices.profiles import PaperPreset", {profiles}),
            ("if TYPE_CHECKING:\n    pass\nelse:\n    from ..devices.profiles import PaperPreset", {profiles}),
            ("if TYPE_CHECKING:\n    from ..devices.profiles import PaperPreset, PrinterProfile", {profiles}),
            ("if TYPE_CHECKING:\n    from ..devices.profiles import *", {profiles}),
            ("if TYPE_CHECKING:\n    import timiniprint.devices.profiles", {profiles}),
            ("if TYPE_CHECKING:\n    from ..devices.profiles import PaperPreset\nfrom ..devices.profiles import PrinterProfile", {profiles}),
            ("if TYPE_CHECKING:\n    from ..devices.catalog import PrinterCatalog", {"timiniprint.devices.catalog"}),
        ]
        with tempfile.TemporaryDirectory() as directory, patch(f"{__name__}.REPO_ROOT", Path(directory)):
            path = Path(directory) / "timiniprint" / "protocol" / "job.py"
            path.parent.mkdir(parents=True)
            for source, expected in cases:
                with self.subTest(source=source):
                    path.write_text(source, encoding="utf-8")
                    imported = _imported_modules(path, allow_paper_preset_typing=True)
                    self.assertEqual({name for name in imported if name.startswith("timiniprint.devices")}, expected)
                    self.assertTrue(_imported_modules(path) & (expected or {profiles}))

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

    def test_transport_does_not_parse_protocol_packets(self) -> None:
        self._assert_no_forbidden_imports(
            PACKAGE_ROOT / "transport",
            [
                "timiniprint.protocol.families",
                "timiniprint.protocol.packet",
            ],
        )

    def test_bluetooth_adapters_do_not_select_protocol_families(self) -> None:
        self._assert_no_forbidden_imports(
            PACKAGE_ROOT / "transport" / "bluetooth" / "adapters",
            ["timiniprint.protocol"],
        )


if __name__ == "__main__":
    unittest.main()
