from __future__ import annotations

from pathlib import Path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
TEXT_EXTENSIONS = {".txt"}
SUPPORTED_DOCUMENT_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS | TEXT_EXTENSIONS


def document_kind(source: str, mime_type: str | None = None, name: str | None = None) -> str | None:
    mime = (mime_type or "").lower()
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("text/"):
        return "text"
    suffix = Path(name or source).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return document_kind(source, mime_type) if name is not None else None


def normalized_width(width: int) -> int:
    return width if width % 8 == 0 else width - (width % 8)


def mm_to_px(mm: int, dpi: int) -> int:
    return 0 if mm <= 0 else max(0, round(mm * dpi / 25.4))
