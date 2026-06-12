from __future__ import annotations

from typing import List, Optional, Protocol, Sequence

from PIL import Image

from .base import Page, PageSource, RasterConverter

DEFAULT_RENDER_DPI = 200


class PdfDocument(Protocol):
    @property
    def page_count(self) -> int: ...

    def render_page(self, index: int, scale: float) -> Image.Image: ...

    def close(self) -> None: ...


class PdfRenderer(Protocol):
    def open(self, path: str) -> PdfDocument: ...


class Pypdfium2PdfRenderer:
    def open(self, path: str) -> PdfDocument:
        import pypdfium2 as pdfium

        return Pypdfium2PdfDocument(pdfium.PdfDocument(path))


class Pypdfium2PdfDocument:
    def __init__(self, document) -> None:
        self._document = document

    @property
    def page_count(self) -> int:
        return len(self._document)

    def render_page(self, index: int, scale: float) -> Image.Image:
        page = self._get_page(index)
        try:
            return self._render_page_to_pil(page, scale)
        finally:
            self._close_page(page)

    def close(self) -> None:
        close = getattr(self._document, "close", None)
        if callable(close):
            close()

    def _get_page(self, index: int):
        try:
            return self._document[index]
        except Exception:
            return self._document.get_page(index)

    @staticmethod
    def _close_page(page) -> None:
        close = getattr(page, "close", None)
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
        raise RuntimeError("PDF render did not return a PIL image")


class PdfConverter(RasterConverter):
    def __init__(
        self,
        page_selection: Optional[str] = None,
        page_gap_px: int = 0,
        trim_side_margins: bool = True,
        trim_top_bottom_margins: bool = True,
        render_dpi: int = DEFAULT_RENDER_DPI,
        pdf_renderer: PdfRenderer | None = None,
        rotate_90_clockwise: bool = False,
    ) -> None:
        super().__init__(
            trim_side_margins=trim_side_margins,
            trim_top_bottom_margins=trim_top_bottom_margins,
            rotate_90_clockwise=rotate_90_clockwise,
        )
        self._page_selection = page_selection
        self._page_gap_px = max(0, int(page_gap_px or 0))
        self._render_dpi = render_dpi
        self._pdf_renderer = pdf_renderer or Pypdfium2PdfRenderer()

    def open(self, path: str, width: int) -> PageSource:
        doc = self._pdf_renderer.open(path)
        try:
            total_pages = doc.page_count
            if total_pages <= 0:
                raise RuntimeError("PDF has no pages")
            return PdfPageSource(
                document=doc,
                page_indexes=self.select_page_indexes(total_pages),
                width=width,
                page_gap_px=self._page_gap_px,
                render_dpi=self._render_dpi,
                converter=self,
            )
        except Exception:
            doc.close()
            raise

    def select_page_indexes(self, total_pages: int) -> Sequence[int]:
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


class PdfPageSource(PageSource):
    """Random-access PDF page source. Pages render lazily and the document closes via close/context manager."""

    def __init__(
        self,
        document: PdfDocument,
        page_indexes: Sequence[int],
        width: int,
        page_gap_px: int,
        render_dpi: int,
        converter: PdfConverter,
    ) -> None:
        self._document = document
        self._page_indexes = list(page_indexes)
        self._width = width
        self._page_gap_px = page_gap_px
        self._render_dpi = render_dpi
        self._converter = converter
        self._closed = False

    @property
    def page_count(self) -> int:
        return len(self._page_indexes)

    @property
    def source_page_count(self) -> int:
        return self._document.page_count

    def source_index(self, index: int) -> int | None:
        return self._page_indexes[index]

    def page(self, index: int) -> Page:
        img = self._document.render_page(self._page_indexes[index], self._render_dpi / 72.0)
        img = self._converter._normalize_image(img)
        img = self._converter._maybe_trim_margins(img)
        img = self._converter._rotate_image(img)
        img = self._converter._resize_to_width(img, self._width)
        if self._page_gap_px > 0 and index < len(self._page_indexes) - 1:
            img = self._converter._append_page_gap(img, self._page_gap_px)
        return Page(img, dither=True, is_text=False)

    def close(self) -> None:
        if not self._closed:
            self._document.close()
            self._closed = True
