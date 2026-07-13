from __future__ import annotations

from pathlib import Path


_BUNDLED_LICENSES = Path(__file__).resolve().parent / "data" / "THIRD_PARTY_LICENSES.txt"
_SOURCE_FILES = ("NOTICE", "LICENSE", "THIRD_PARTY_NOTICES.md")


def license_text() -> str:
    if _BUNDLED_LICENSES.is_file():
        return _BUNDLED_LICENSES.read_text(encoding="utf-8")

    repository = Path(__file__).resolve().parents[1]
    sections = []
    for filename in _SOURCE_FILES:
        path = repository / filename
        if path.is_file():
            sections.append(f"{'=' * 78}\n{filename}\n{'=' * 78}\n{path.read_text(encoding='utf-8').rstrip()}")
    return "\n\n".join(sections).rstrip() + "\n"
