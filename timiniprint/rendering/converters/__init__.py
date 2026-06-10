from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set

from .base import ImageLoader, Page, PageConverter, PageSource
from .image import ImageConverter
from .pdf import PdfConverter, PdfRenderer
from .text import TextConverter
from ..formats import IMAGE_EXTENSIONS, SUPPORTED_DOCUMENT_EXTENSIONS

SUPPORTED_EXTENSIONS: Set[str] = SUPPORTED_DOCUMENT_EXTENSIONS


class PageLoader:
    def __init__(
        self,
        converters: Optional[Dict[str, PageConverter]] = None,
        text_font: Optional[str] = None,
        text_columns: Optional[int] = None,
        text_wrap: bool = True,
        trim_side_margins: bool = True,
        trim_top_bottom_margins: bool = True,
        pdf_pages: Optional[str] = None,
        pdf_page_gap_px: int = 0,
        pdf_renderer: PdfRenderer | None = None,
        image_loader: ImageLoader | None = None,
    ) -> None:
        if converters is None:
            converters = {}
            image_converter = ImageConverter(
                image_loader=image_loader,
                trim_side_margins=trim_side_margins,
                trim_top_bottom_margins=trim_top_bottom_margins,
            )
            for ext in IMAGE_EXTENSIONS:
                converters[ext] = image_converter
            converters[".pdf"] = PdfConverter(
                page_selection=pdf_pages,
                page_gap_px=pdf_page_gap_px,
                trim_side_margins=trim_side_margins,
                trim_top_bottom_margins=trim_top_bottom_margins,
                pdf_renderer=pdf_renderer,
            )
            converters[".txt"] = TextConverter(
                font_path=text_font,
                columns=text_columns,
                wrap_lines=text_wrap,
            )
        self._converters = converters

    @property
    def supported_extensions(self) -> Set[str]:
        return set(self._converters.keys())

    def open(self, path: str, width: int) -> PageSource:
        ext = Path(path).suffix.lower()
        converter = self._converters.get(ext)
        if not converter:
            raise ValueError(f"Unsupported file extension: {ext}")
        return converter.open(path, width)

    def load(self, path: str, width: int) -> List[Page]:
        with self.open(path, width) as source:
            return list(source)


def load_pages(path: str, width: int) -> List[Page]:
    return PageLoader().load(path, width)


__all__ = ["Page", "PageLoader", "PageSource", "PdfRenderer", "SUPPORTED_EXTENSIONS", "load_pages"]
