import asyncio
import errno
import sys
import traceback
import types

import pytest
from PIL import Image

from tests.helpers import build_capture_reporter
from timiniprint.devices import PrinterCatalog
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.document_renderer import DocumentRenderer, RenderDocument
from timiniprint.printing.settings import PrintSettings
from timiniprint.rendering import DocumentReadError
from timiniprint.reporting import ReportLevel


@pytest.fixture(scope="module")
def device():
    return PrinterCatalog.load().device_from_profile("v5x")


def read_document(renderer, path, operation, device, reporter):
    settings = PrintSettings()
    if operation in ("build", "iterate"):
        builder = PrintJobBuilder(device, document_renderer=renderer, reporter=reporter)
        return builder.build_from_file(str(path)) if operation == "build" else list(builder.iter_page_jobs(str(path)))
    document = RenderDocument(str(path))
    if operation == "source_preview":
        return renderer.preview_source_page(document, target_width=384)
    plan = renderer.plan_document(document, device, settings)
    if operation == "plan":
        return plan
    return getattr(renderer, operation)(plan, plan.pages[0], device, settings)


def check_read_failure(error, reporter, sink, path, min_level, native_type):
    assert not isinstance(error, OSError)
    reporter.error(exc=error)
    normal = [message for message in sink.messages if message.level != ReportLevel.TRACE]
    assert len(normal) == 1
    assert normal[0].level == ReportLevel.ERROR
    assert "Cannot read" in normal[0].detail
    assert error.__cause__ is None
    assert error.__suppress_context__
    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    for text in (str(error), repr(error), formatted, normal[0].short, normal[0].detail):
        assert str(path) not in text
        assert path.name not in text
    traces = [message for message in sink.messages
              if message.level == ReportLevel.TRACE and message.short == "Document read failed"]
    assert len(traces) == int(min_level == ReportLevel.TRACE)
    if traces:
        assert isinstance(traces[0].exc, native_type)
        assert path.name in traces[0].detail


@pytest.mark.parametrize("min_level", [ReportLevel.DEBUG, ReportLevel.TRACE])
@pytest.mark.parametrize("operation", ["source_preview", "preview_page", "print_page", "build", "iterate"])
def test_corrupt_image_name_is_only_in_trace(tmp_path, device, min_level, operation):
    from PIL import UnidentifiedImageError

    path = tmp_path / "private-customer-document.png"
    path.write_bytes(b"not an image")
    reporter, sink = build_capture_reporter(min_level=min_level)
    renderer = DocumentRenderer(reporter=reporter)
    with pytest.raises(DocumentReadError) as caught:
        read_document(renderer, path, operation, device, reporter)
    check_read_failure(caught.value, reporter, sink, path, min_level, UnidentifiedImageError)


@pytest.mark.parametrize("min_level", [ReportLevel.DEBUG, ReportLevel.TRACE])
@pytest.mark.parametrize("operation", ["plan", "source_preview", "build", "iterate"])
def test_text_permission_error_does_not_copy_filename_or_strerror(tmp_path, device, min_level, operation):
    path = tmp_path / "private-customer-document.txt"
    path.write_text("text", encoding="utf-8")
    native = PermissionError(errno.EACCES, f"cannot read {path}", str(path))

    def read_text(source):
        assert source == str(path)
        raise native

    reporter, sink = build_capture_reporter(min_level=min_level)
    renderer = DocumentRenderer(text_loader=read_text, reporter=reporter)
    with pytest.raises(DocumentReadError) as caught:
        read_document(renderer, path, operation, device, reporter)
    check_read_failure(caught.value, reporter, sink, path, min_level, PermissionError)
    assert "Permission denied" in str(caught.value)


@pytest.mark.parametrize("min_level", [ReportLevel.DEBUG, ReportLevel.TRACE])
def test_builder_passes_reporter_to_default_renderer(tmp_path, device, min_level):
    from PIL import UnidentifiedImageError

    path = tmp_path / "private-default-renderer.png"
    path.write_bytes(b"not an image")
    reporter, sink = build_capture_reporter(min_level=min_level)
    builder = PrintJobBuilder(device, reporter=reporter)
    with pytest.raises(DocumentReadError) as caught:
        builder.build_from_file(str(path))
    reporter.error(exc=caught.value)
    normal = [message.detail for message in sink.messages if message.level != ReportLevel.TRACE]
    assert all(path.name not in detail for detail in normal)
    failures = [message for message in sink.messages if message.short == "Document read failed"]
    assert len(failures) == int(min_level == ReportLevel.TRACE)
    if failures:
        assert isinstance(failures[0].exc, UnidentifiedImageError)
        assert path.name in failures[0].detail


@pytest.fixture
def native_pdf(monkeypatch):
    class PdfiumError(RuntimeError):
        pass

    module = types.ModuleType("pypdfium2")
    module.PdfiumError = PdfiumError
    monkeypatch.setitem(sys.modules, "pypdfium2", module)
    return module


@pytest.mark.parametrize("missing", [False, True])
def test_real_pdf_decoder_does_not_expose_source_in_public_failure(tmp_path, device, missing):
    pdfium = pytest.importorskip("pypdfium2")
    path = tmp_path / "private-native-backend.pdf"
    if not missing:
        path.write_bytes(b"not a PDF")
    reporter, sink = build_capture_reporter(min_level=ReportLevel.TRACE)
    renderer = DocumentRenderer(reporter=reporter)
    with pytest.raises(DocumentReadError) as caught:
        renderer.plan_document(RenderDocument(str(path)), device, PrintSettings())
    reporter.error(exc=caught.value)
    trace, normal = sink.messages
    assert trace.level == ReportLevel.TRACE
    assert isinstance(trace.exc, FileNotFoundError if missing else pdfium.PdfiumError)
    assert normal.level == ReportLevel.ERROR
    assert path.name not in normal.detail
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("min_level", [ReportLevel.DEBUG, ReportLevel.TRACE])
@pytest.mark.parametrize("operation", ["source_preview", "preview_page", "print_page", "build", "iterate"])
@pytest.mark.parametrize("stage", ["open", "page", "close"])
def test_pdf_native_failures_are_safe_and_close_document(tmp_path, device, native_pdf, min_level, operation, stage):
    path = tmp_path / "private-customer-document.pdf"
    path.write_bytes(b"PDF")
    error = (PermissionError(errno.EACCES, "access denied", str(path)) if stage == "close"
             else native_pdf.PdfiumError(f"Cannot decode {path}"))
    opened = []

    class Page:
        def render_topil(self, scale):
            if stage == "close":
                return Image.new("L", (10, 10), 255)
            raise error

        def close(self):
            pass

    class Pdf:
        def __init__(self, source):
            assert source == str(path)
            if stage == "open":
                raise error
            opened.append(self)
            self.closed = False

        def __len__(self):
            return 1

        def __getitem__(self, index):
            return Page()

        def close(self):
            self.closed = True
            if stage == "close":
                raise error

    native_pdf.PdfDocument = Pdf
    reporter, sink = build_capture_reporter(min_level=min_level)
    renderer = DocumentRenderer(reporter=reporter)
    with pytest.raises(DocumentReadError) as caught:
        read_document(renderer, path, operation, device, reporter)
    check_read_failure(caught.value, reporter, sink, path, min_level, type(error))
    assert all(document.closed for document in opened)


@pytest.mark.parametrize("error", [TypeError("programming bug"), ValueError("invalid setting"),
                                  RuntimeError("programming bug"), asyncio.CancelledError()])
def test_unrelated_errors_and_cancellation_are_not_converted(device, error):
    def load_image(source):
        raise error

    reporter, sink = build_capture_reporter(min_level=ReportLevel.TRACE)
    renderer = DocumentRenderer(image_loader=load_image, reporter=reporter)
    plan = renderer.plan_document(RenderDocument("image.png"), device, PrintSettings())
    with pytest.raises(type(error)) as caught:
        renderer.print_page(plan, plan.pages[0], device, PrintSettings())
    assert caught.value is error
    assert sink.messages == []
