from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PIL import Image

from ..devices.device import PrinterDevice
from ..protocol import ImagePipelineConfig, PageFlow
from ..protocol.job import PrinterProtocol
from ..protocol.runtime import RuntimePrintCapabilities
from ..raster import DitherMode, RasterSet
from ..rendering.converters import Page
from ..rendering.converters.base import ImageLoader, PageSource
from ..rendering.converters.image import ImageConverter
from ..rendering.converters.pdf import PdfConverter, PdfRenderer
from ..rendering.converters.text import TextConverter
from ..rendering.formats import document_kind, mm_to_px
from ..rendering.renderer import PrintImageRenderer
from .paper import ResolvedPaper, resolve_paper
from .settings import PrintSettings, resolve_gray_preprocessing

TextFontResolver = Callable[[Optional[str]], Optional[str]]
TextLoader = Callable[[str], str]

TEXT_DOCUMENT_NAME = "<text>"


@dataclass(frozen=True)
class RenderDocument:
    source: str
    mime_type: str | None = None
    name: str = ""


@dataclass(frozen=True)
class DocumentPage:
    index: int
    source_index: int | None = None
    label: str = ""

    @property
    def number(self) -> int:
        return self.index + 1


@dataclass(frozen=True)
class DocumentPlan:
    document: RenderDocument
    kind: str
    pages: tuple[DocumentPage, ...]
    source_page_count: int | None = None
    page_flow: PageFlow = PageFlow.PAGED

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass(frozen=True)
class PreviewPage:
    png: bytes
    width: int
    height: int
    raster_width: int
    raster_height: int
    page_count: int = 1
    page_number: int = 1


@dataclass(frozen=True)
class RenderedPage:
    source_page: Page
    raster_set: RasterSet
    image_pipeline: ImagePipelineConfig
    is_text: bool = False
    dither_mode: DitherMode = DitherMode.NONE
    gamma_handle: bool = False
    gamma_value: float | None = None


class DocumentRenderer:
    def __init__(
        self,
        *,
        image_loader: ImageLoader | None = None,
        pdf_renderer: PdfRenderer | None = None,
        image_renderer: PrintImageRenderer | None = None,
        text_font_resolver: TextFontResolver | None = None,
        text_loader: TextLoader | None = None,
        text_page_height_to_width: float | None = None,
    ) -> None:
        self.image_loader = image_loader
        self.pdf_renderer = pdf_renderer
        self.image_renderer = image_renderer or PrintImageRenderer()
        self.text_font_resolver = text_font_resolver or (lambda key: key)
        self.text_loader = text_loader or _load_text
        self.text_page_height_to_width = text_page_height_to_width

    def plan_text(
        self,
        text: str,
        device: PrinterDevice,
        settings: PrintSettings,
    ) -> DocumentPlan:
        return self.plan_document(_text_document(text), device, settings)

    def plan_document(
        self,
        document: RenderDocument,
        device: PrinterDevice,
        settings: PrintSettings,
    ) -> DocumentPlan:
        kind = self._document_kind(document)
        if kind not in ("image", "pdf", "text"):
            raise ValueError("Supported file formats: png, jpg, jpeg, gif, bmp, webp, pdf, txt")
        if kind == "image":
            return DocumentPlan(
                document=document,
                kind=kind,
                pages=(DocumentPage(0, source_index=0, label="1"),),
                source_page_count=1,
                page_flow=PageFlow.PAGED,
            )
        planned_document = (
            _text_document(self._text_content(document))
            if kind == "text"
            else document
        )
        with self._open_source(planned_document, kind, device, settings) as source:
            return DocumentPlan(
                document=planned_document,
                kind=kind,
                pages=tuple(
                    DocumentPage(
                        index,
                        source_index=source.source_index(index),
                        label=str((source.source_index(index) or index) + 1),
                    )
                    for index in range(source.page_count)
                ),
                source_page_count=source.source_page_count,
                page_flow=PageFlow.CONTINUOUS if kind == "text" else PageFlow.PAGED,
            )

    def preview_page(
        self,
        plan: DocumentPlan,
        page: DocumentPage,
        device: PrinterDevice,
        settings: PrintSettings,
    ) -> PreviewPage:
        vendor_page = self._open_vendor_page(plan, page, device, settings)
        pipeline, dither_mode, gamma_handle, gamma_value = self._render_options(
            vendor_page,
            device,
            settings,
        )
        preview = self.image_renderer.preview_image(
            vendor_page.image,
            pipeline.default_format,
            dither_mode=dither_mode,
            gamma_handle=gamma_handle,
            gamma_value=gamma_value,
        )
        return PreviewPage(
            png=PrintImageRenderer.image_png(preview),
            width=preview.width,
            height=preview.height,
            raster_width=preview.width,
            raster_height=preview.height,
            page_count=plan.page_count,
            page_number=page.index + 1,
        )

    def print_page(
        self,
        plan: DocumentPlan,
        page: DocumentPage,
        device: PrinterDevice,
        settings: PrintSettings,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
    ) -> RenderedPage:
        vendor_page = self._open_vendor_page(plan, page, device, settings)
        pipeline, dither_mode, gamma_handle, gamma_value = self._render_options(
            vendor_page,
            device,
            settings,
            runtime_capabilities=runtime_capabilities,
        )
        return RenderedPage(
            source_page=vendor_page,
            raster_set=self.image_renderer.raster_set(
                vendor_page.image,
                (pipeline.default_format,),
                dither_mode=dither_mode,
                gamma_handle=gamma_handle,
                gamma_value=gamma_value,
            ),
            image_pipeline=pipeline,
            is_text=settings.text_mode if settings.text_mode is not None else vendor_page.is_text,
            dither_mode=dither_mode,
            gamma_handle=gamma_handle,
            gamma_value=gamma_value,
        )

    def _open_vendor_page(
        self,
        plan: DocumentPlan,
        page: DocumentPage,
        device: PrinterDevice,
        settings: PrintSettings,
    ) -> Page:
        paper = resolve_paper(device, settings)
        with self._open_source(plan.document, plan.kind, device, settings) as source:
            if page.index < 0 or page.index >= source.page_count:
                raise IndexError(f"Document page out of range: {page.number}")
            return self._apply_paper_layout(source.page(page.index), paper)

    def _apply_paper_layout(self, page: Page, paper: ResolvedPaper) -> Page:
        if paper.raster_height_px is not None and page.image.height > paper.raster_height_px:
            raise ValueError(
                f"Rendered page height {page.image.height}px exceeds paper raster height "
                f"{paper.raster_height_px}px"
            )
        final_width = (
            paper.paper_width_px
            if not paper.left_padding_px and paper.paper_width_px > page.image.width
            else page.image.width
        )
        final_height = paper.raster_height_px or page.image.height
        if (final_width, final_height) == page.image.size:
            return page
        canvas = Image.new(
            page.image.mode,
            (final_width, final_height),
            _white_for_mode(page.image.mode),
        )
        canvas.paste(page.image, ((final_width - page.image.width) // 2, 0))
        return Page(canvas, dither=page.dither, is_text=page.is_text)

    def _open_source(
        self,
        document: RenderDocument,
        kind: str,
        device: PrinterDevice,
        settings: PrintSettings,
    ) -> PageSource:
        width = resolve_paper(device, settings).render_width_px
        if kind == "text":
            return TextConverter(
                font_path=self.text_font_resolver(settings.text_font),
                columns=settings.text_columns,
                wrap_lines=settings.text_wrap,
                page_height_to_width=self.text_page_height_to_width,
                rotate_90_clockwise=settings.rotate_90_clockwise,
            ).open_text(self._text_content(document), width)
        if kind == "image":
            return ImageConverter(
                image_loader=self.image_loader,
                trim_side_margins=settings.trim_side_margins,
                trim_top_bottom_margins=settings.trim_top_bottom_margins,
                rotate_90_clockwise=settings.rotate_90_clockwise,
            ).open(document.source, width)
        if kind == "pdf":
            return PdfConverter(
                page_selection=settings.pdf_pages,
                page_gap_px=mm_to_px(settings.page_gap_mm, device.profile.dev_dpi),
                trim_side_margins=settings.trim_side_margins,
                trim_top_bottom_margins=settings.trim_top_bottom_margins,
                pdf_renderer=self.pdf_renderer,
                rotate_90_clockwise=settings.rotate_90_clockwise,
            ).open(document.source, width)
        raise ValueError("Supported file formats: png, jpg, jpeg, gif, bmp, webp, pdf, txt")

    def _render_options(
        self,
        page: Page,
        device: PrinterDevice,
        settings: PrintSettings,
        *,
        runtime_capabilities: RuntimePrintCapabilities | None = None,
    ) -> tuple[ImagePipelineConfig, DitherMode, bool, float | None]:
        pipeline = PrinterProtocol(device).resolve_image_pipeline(
            image_encoding_override=settings.image_encoding_override,
            pixel_format_override=settings.pixel_format_override,
            runtime_capabilities=runtime_capabilities,
        )
        gamma_handle, gamma_value = resolve_gray_preprocessing(
            settings,
            device.protocol_family,
            pipeline.encoding,
        )
        return (
            pipeline,
            settings.dither_mode if page.dither else DitherMode.NONE,
            gamma_handle,
            gamma_value,
        )

    def _document_kind(self, document: RenderDocument) -> str | None:
        return document_kind(document.source, document.mime_type, document.name)

    def _text_content(self, document: RenderDocument) -> str:
        return document.source if document.name == TEXT_DOCUMENT_NAME else self.text_loader(document.source)


def _text_document(text: str) -> RenderDocument:
    return RenderDocument(text, "text/plain", TEXT_DOCUMENT_NAME)


def _load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def _white_for_mode(mode: str) -> str | int | tuple[int, ...]:
    if mode == "1":
        return 1
    if mode == "L":
        return 255
    if mode == "RGBA":
        return (255, 255, 255, 255)
    return "white"
