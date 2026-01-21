from __future__ import annotations

from typing import List, Optional, Sequence

from PIL import Image

from .base import Page, RasterConverter

DEFAULT_RENDER_DPI = 200


class PdfConverter(RasterConverter):
    def __init__(
        self,
        page_selection: Optional[str] = None,
        page_gap_px: int = 0,
        trim_side_margins: bool = True,
        trim_top_bottom_margins: bool = True,
        render_dpi: int = DEFAULT_RENDER_DPI,
    ) -> None:
        super().__init__(
            trim_side_margins=trim_side_margins,
            trim_top_bottom_margins=trim_top_bottom_margins,
        )
        self._page_selection = page_selection
        self._page_gap_px = max(0, int(page_gap_px or 0))
        self._render_dpi = render_dpi

    def load(self, path: str, width: int) -> List[Page]:
        pages = self._load_pdf_pages(path)
        out: List[Page] = []
        last_index = len(pages) - 1
        for idx, page in enumerate(pages):
            img = self._normalize_image(page)
            img = self._maybe_trim_margins(img)
            img = self._resize_to_width(img, width)
            if self._page_gap_px > 0 and idx < last_index:
                img = self._append_page_gap(img, self._page_gap_px)
            out.append(Page(img, dither=True, is_text=False))
        return out

    def _load_pdf_pages(self, path: str) -> List[Image.Image]:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(path)
        pages: List[Image.Image] = []
        try:
            total_pages = len(doc)
            if total_pages <= 0:
                raise RuntimeError("PDF has no pages")
            page_indexes = self._select_page_indexes(total_pages)
            scale = self._render_dpi / 72.0
            for index in page_indexes:
                page = self._get_pdf_page(doc, index)
                try:
                    pages.append(self._render_page_to_pil(page, scale))
                finally:
                    self._close_pdf_page(page)
        finally:
            self._close_pdf_document(doc)
        if not pages:
            raise RuntimeError("PDF render failed (no pages)")
        return pages

    @staticmethod
    def _get_pdf_page(doc, index: int):
        try:
            return doc[index]
        except Exception:
            return doc.get_page(index)

    @staticmethod
    def _close_pdf_page(page) -> None:
        close = getattr(page, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _close_pdf_document(doc) -> None:
        close = getattr(doc, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _render_page_to_pil(page, scale: float) -> Image.Image:
        if hasattr(page, "render_topil"):
            try:
                return page.render_topil(scale=scale)
            except TypeError:
                return page.render_topil(scale)
        try:
            bitmap = page.render(scale=scale)
        except TypeError:
            bitmap = page.render(scale)
        to_pil = getattr(bitmap, "to_pil", None)
        if callable(to_pil):
            return to_pil()
        raise RuntimeError("pypdfium2 render did not return a PIL image")

    def _select_page_indexes(self, total_pages: int) -> Sequence[int]:
        selection = (self._page_selection or "").strip()
        if not selection:
            return list(range(total_pages))
        tokens = [token.strip() for token in selection.split(",") if token.strip()]
        if not tokens:
            return list(range(total_pages))
        requested: List[int] = []
        for token in tokens:
            if "-" in token:
                start_str, end_str = token.split("-", 1)
                start_str = start_str.strip()
                end_str = end_str.strip()
                if not (start_str.isdigit() and end_str.isdigit()):
                    raise ValueError(f"Invalid PDF page range: {token}")
                start = int(start_str)
                end = int(end_str)
                if start < 1 or end < 1:
                    raise ValueError("PDF pages start at 1")
                if start > end:
                    raise ValueError(f"Invalid PDF page range: {token}")
                requested.extend(range(start, end + 1))
                continue
            if not token.isdigit():
                raise ValueError(f"Invalid PDF page selection: {token}")
            requested.append(int(token))
        page_indexes: List[int] = []
        for page in requested:
            if page < 1 or page > total_pages:
                raise ValueError(f"PDF page {page} out of range (1-{total_pages})")
            index = page - 1
            if index not in page_indexes:
                page_indexes.append(index)
        if not page_indexes:
            raise ValueError("No PDF pages selected")
        return page_indexes

    @staticmethod
    def _append_page_gap(img: Image.Image, gap: int) -> Image.Image:
        if gap <= 0:
            return img
        fill = 255 if img.mode == "L" else (255, 255, 255)
        out = Image.new(img.mode, (img.width, img.height + gap), fill)
        out.paste(img, (0, 0))
        return out
