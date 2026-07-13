from __future__ import annotations

import argparse
import re
from collections import deque
from importlib import metadata
from pathlib import Path


_PROJECT_FILES = ("NOTICE", "LICENSE", "THIRD_PARTY_NOTICES.md")
_LICENSE_PREFIXES = ("license", "copying", "notice", "copyright")
_CANONICAL_SEPARATOR = re.compile(r"[-_.]+")


def build_license_text(output: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    distributions = _resolve_runtime_distributions(repository / "requirements.txt")
    distributions.append(metadata.distribution("PyInstaller"))

    sections = [
        "TiMini-Print license information",
        "Generated for the dependency set installed on this build platform.",
    ]
    for filename in _PROJECT_FILES:
        sections.append(
            _section(filename, (repository / filename).read_text(encoding="utf-8"))
        )

    manifest: list[str] = []
    license_sections: list[str] = []
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
        files = _distribution_license_files(distribution, repository / "licenses")
        if not files:
            raise RuntimeError(f"no license files found for {name} {distribution.version}")
        for label, path in files:
            license_sections.append(
                _section(
                    f"{name} {distribution.version} — {label}",
                    path.read_text(encoding="utf-8", errors="replace"),
                )
            )

    sections.append(_section("Installed component manifest", "\n".join(manifest)))
    sections.extend(license_sections)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(sections).rstrip() + "\n", encoding="utf-8")


def _resolve_runtime_distributions(requirements_path: Path) -> list[metadata.Distribution]:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement

    environment = default_environment()
    queue: deque[str] = deque()
    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if not value or value.startswith("-"):
            continue
        requirement = Requirement(value)
        if requirement.marker is None or requirement.marker.evaluate(environment):
            queue.append(requirement.name)

    resolved: dict[str, metadata.Distribution] = {}
    while queue:
        requested_name = queue.popleft()
        canonical_name = _canonicalize_name(requested_name)
        if canonical_name in resolved:
            continue
        distribution = metadata.distribution(requested_name)
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
) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
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
    fallback = _static_license_directory(static_licenses, canonical_name)
    if fallback.is_dir():
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
    return path.name.lower().startswith(_LICENSE_PREFIXES)


def _canonicalize_name(name: str) -> str:
    return _CANONICAL_SEPARATOR.sub("-", name).lower()


def _static_license_directory(root: Path, canonical_name: str) -> Path:
    if canonical_name.startswith("pyobjc-"):
        return root / "pyobjc"
    if canonical_name.startswith("winrt-"):
        return root / "winrt"
    return root / canonical_name


def _section(title: str, text: str) -> str:
    separator = "=" * 78
    return f"{separator}\n{title}\n{separator}\n{text.rstrip()}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build one platform-specific license text for a frozen executable."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_license_text(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
