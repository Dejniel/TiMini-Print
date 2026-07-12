from __future__ import annotations

import argparse
import shutil
from collections import deque
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


_PROJECT_FILES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")
_BUILD_DISTRIBUTIONS = ("PyInstaller",)
_LICENSE_PREFIXES = ("license", "copying", "notice", "copyright")


def prepare_release_package(artifact: Path, output: Path) -> None:
    if not artifact.exists():
        raise FileNotFoundError(f"release artifact does not exist: {artifact}")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    destination = output / artifact.name
    if artifact.is_dir():
        shutil.copytree(artifact, destination, symlinks=True)
    else:
        shutil.copy2(artifact, destination)

    repository = Path(__file__).resolve().parents[1]
    for filename in _PROJECT_FILES:
        shutil.copy2(repository / filename, output / filename)

    distributions = _resolve_distributions(repository / "requirements.txt")
    licenses = output / "licenses" / "python"
    manifest = _copy_distribution_licenses(
        distributions,
        licenses,
        repository / "licenses",
    )
    (output / "THIRD_PARTY_MANIFEST.txt").write_text(
        "\n".join(manifest) + "\n",
        encoding="utf-8",
    )


def _resolve_distributions(requirements_path: Path) -> list[metadata.Distribution]:
    environment = default_environment()
    queue: deque[str] = deque(_BUILD_DISTRIBUTIONS)
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
        canonical_name = canonicalize_name(requested_name)
        if canonical_name in resolved:
            continue
        distribution = metadata.distribution(requested_name)
        resolved[canonical_name] = distribution

        for value in distribution.requires or ():
            requirement = Requirement(value)
            marker_environment = dict(environment, extra="")
            if requirement.marker is None or requirement.marker.evaluate(marker_environment):
                queue.append(requirement.name)

    return [resolved[name] for name in sorted(resolved)]


def _copy_distribution_licenses(
    distributions: list[metadata.Distribution],
    destination: Path,
    static_licenses: Path,
) -> list[str]:
    manifest: list[str] = []
    for distribution in distributions:
        name = distribution.metadata["Name"]
        canonical_name = canonicalize_name(name)
        copied = 0
        for entry in distribution.files or ():
            relative = Path(str(entry))
            if not _is_license_file(relative):
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            target = destination / canonical_name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1

        fallback = static_licenses / canonical_name
        if copied == 0 and fallback.is_dir():
            shutil.copytree(fallback, destination / canonical_name, dirs_exist_ok=True)
            copied = sum(1 for path in fallback.rglob("*") if path.is_file())

        if copied == 0:
            raise RuntimeError(f"no license files found for {name} {distribution.version}")

        license_name = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "see bundled license files"
        )
        manifest.append(f"{name}=={distribution.version} | {license_name}")
    return manifest


def _is_license_file(path: Path) -> bool:
    lowered_parts = tuple(part.lower() for part in path.parts)
    for index, part in enumerate(lowered_parts):
        if part == "licenses" and any(
            parent.endswith(".dist-info") for parent in lowered_parts[:index]
        ):
            return True
    return path.name.lower().startswith(_LICENSE_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a release directory with required license files."
    )
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    prepare_release_package(args.artifact, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
