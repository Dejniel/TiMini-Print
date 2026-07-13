from __future__ import annotations

import re
from collections import deque
from importlib import metadata
from pathlib import Path
from typing import List, Tuple


_REPOSITORY = Path(__file__).resolve().parents[1]
_BUNDLED_LICENSES = Path(__file__).resolve().parent / "data" / "THIRD_PARTY_LICENSES.txt"
_PROJECT_FILES = ("NOTICE", "LICENSE", "THIRD_PARTY_NOTICES.md")
_LICENSE_PREFIXES = ("license", "copying", "notice", "copyright")
_LICENSE_SUFFIXES = (".txt", ".md", ".rst", ".html", ".lesser", ".lib")
_CANONICAL_SEPARATOR = re.compile(r"[-_.]+")
_STATIC_LICENSE_FALLBACKS = {
    "pyserial": ("pyserial", frozenset({"3.5"})),
    "winsdk": ("winsdk", frozenset({"1.0.0b10"})),
}
_STATIC_LICENSE_FALLBACK_FAMILIES = (
    ("pyobjc-", "pyobjc", frozenset({"12.2.1"})),
    ("winrt-", "winrt", frozenset({"3.2.1"})),
)


def license_text() -> str:
    if _BUNDLED_LICENSES.is_file():
        return _BUNDLED_LICENSES.read_text(encoding="utf-8")
    return build_license_text(strict=False)


def write_license_text(
    output: Path,
    *,
    additional_distribution_names: Tuple[str, ...] = (),
    strict: bool = True,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_license_text(
            additional_distribution_names=additional_distribution_names,
            strict=strict,
        ),
        encoding="utf-8",
    )


def build_license_text(
    *,
    additional_distribution_names: Tuple[str, ...] = (),
    strict: bool = True,
) -> str:
    distributions = _resolve_runtime_distributions(
        _REPOSITORY / "requirements.txt",
        strict=strict,
    )
    distributions.extend(
        metadata.distribution(name) for name in additional_distribution_names
    )

    manifest: List[str] = []
    license_sections: List[str] = []
    for distribution in sorted(
        distributions,
        key=lambda item: _canonicalize_name(item.metadata["Name"]),
    ):
        name = distribution.metadata["Name"]
        license_name = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "see license text below"
        )
        manifest.append(f"{name}=={distribution.version} | {license_name}")
        files = _distribution_license_files(distribution, _REPOSITORY / "licenses")
        if not files:
            message = f"no license files found for {name} {distribution.version}"
            if strict:
                raise RuntimeError(message)
            license_sections.append(
                _section(
                    f"{name} {distribution.version} — license text unavailable",
                    message,
                )
            )
            continue
        for label, path in files:
            license_sections.append(
                _section(
                    f"{name} {distribution.version} — {label}",
                    path.read_text(encoding="utf-8", errors="replace"),
                )
            )

    sections = [
        "TiMini-Print license information",
        "Generated for the dependency set installed on this platform.",
        _section("Installed component manifest", "\n".join(manifest)),
    ]
    for filename in _PROJECT_FILES:
        sections.append(
            _section(filename, (_REPOSITORY / filename).read_text(encoding="utf-8"))
        )
    sections.extend(license_sections)
    return "\n\n".join(sections).rstrip() + "\n"


def _resolve_runtime_distributions(
    requirements_path: Path,
    *,
    strict: bool,
) -> List[metadata.Distribution]:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement

    environment = default_environment()
    queue = deque()
    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if not value or value.startswith("-"):
            continue
        requirement = Requirement(value)
        if requirement.marker is None or requirement.marker.evaluate(environment):
            queue.append(requirement.name)

    resolved = {}
    while queue:
        requested_name = queue.popleft()
        canonical_name = _canonicalize_name(requested_name)
        if canonical_name in resolved:
            continue
        try:
            distribution = metadata.distribution(requested_name)
        except metadata.PackageNotFoundError:
            if strict:
                raise
            continue
        resolved[canonical_name] = distribution
        for value in distribution.requires or ():
            requirement = Requirement(value)
            marker_environment = dict(environment, extra="")
            if requirement.marker is None or requirement.marker.evaluate(marker_environment):
                queue.append(requirement.name)
    return list(resolved.values())


def _distribution_license_files(
    distribution: metadata.Distribution,
    static_licenses: Path,
) -> List[Tuple[str, Path]]:
    files = []
    for entry in distribution.files or ():
        relative = Path(str(entry))
        if not _is_license_file(relative):
            continue
        source = Path(distribution.locate_file(entry))
        if source.is_file():
            files.append((relative.as_posix(), source))
    if files:
        return files

    canonical_name = _canonicalize_name(distribution.metadata["Name"])
    fallback = _static_license_directory(
        static_licenses,
        canonical_name,
        distribution.version,
    )
    if fallback is not None and fallback.is_dir():
        return [
            (path.relative_to(fallback).as_posix(), path)
            for path in sorted(fallback.rglob("*"))
            if path.is_file()
        ]
    return []


def _is_license_file(path: Path) -> bool:
    lowered_parts = tuple(part.lower() for part in path.parts)
    for index, part in enumerate(lowered_parts):
        if part == "licenses" and any(
            parent.endswith(".dist-info") for parent in lowered_parts[:index]
        ):
            return True
    name = path.name.lower()
    for prefix in _LICENSE_PREFIXES:
        if name == prefix:
            return True
        remainder = name[len(prefix) :] if name.startswith(prefix) else ""
        if remainder.startswith(("-", "_")) or remainder in _LICENSE_SUFFIXES:
            return True
    return False


def _canonicalize_name(name: str) -> str:
    return _CANONICAL_SEPARATOR.sub("-", name).lower()


def _static_license_directory(
    root: Path,
    canonical_name: str,
    version: str,
) -> Path | None:
    fallback = _STATIC_LICENSE_FALLBACKS.get(canonical_name)
    if fallback is not None:
        directory, versions = fallback
        return root / directory if version in versions else None
    for prefix, directory, versions in _STATIC_LICENSE_FALLBACK_FAMILIES:
        if canonical_name.startswith(prefix):
            return root / directory if version in versions else None
    return None


def _section(title: str, text: str) -> str:
    separator = "=" * 78
    return f"{separator}\n{title}\n{separator}\n{text.rstrip()}"
