from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
import tempfile
from typing import TYPE_CHECKING

from .. import reporting
from ..protocol import PrinterProtocol, ProtocolJob
from ..protocol.runtime import RuntimePrintCapabilities
from ..protocol.types import ImagePipelineConfig, PageFlow
from ..raster import RasterSet
from .builder import PrintJobBuilder
from .raster_job import build_raster_page_job as _build_raster_page_job
from .raster_job import combine_raster_page_jobs as _combine_raster_page_jobs
from .runtime.base import PreparedPrinter, RuntimeController
from .runtime.prepare import prepare_connection_runtime
from .send import send_prepared_job
from .settings import PrintSettings

if TYPE_CHECKING:
    from ..devices import PrinterDevice
    from ..transport.base import PrinterConnection, PrinterConnector


@dataclass(frozen=True, init=False)
class ConnectedPrinter:
    """Resolved printer with an open connection and prepared protocol runtime.

    Normally obtain this object through ``connect_printer()``, not its low-level
    constructor. Capability accessors and raster job builders perform no I/O;
    print/send/motion methods communicate through the prepared connection.
    Use ``async with`` or call ``disconnect()`` to release it.

    Blocking device conditions (e.g. paper out) raise ``PrinterNotReadyError``
    with machine-readable reasons, not a transport error. A failed operation
    may already have printed part of the job; it is not retried automatically.
    Print failures do not close this session. Exiting its context still does.
    """

    _connection: "PrinterConnection"
    _prepared: PreparedPrinter
    _reporter: reporting.Reporter = reporting.DUMMY_REPORTER

    def __init__(
        self,
        connection: "PrinterConnection",
        prepared: PreparedPrinter,
        *,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> None:
        """Wrap a connection whose runtime has already been prepared by the caller."""
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_prepared", prepared)
        object.__setattr__(self, "_reporter", reporter)

    async def send_job(self, job: ProtocolJob, *, timeout: float = 1.0) -> None:
        """Send a prepared job, preserving its steps and runtime completion policy.

        The job must have been built for this resolved device and session.
        ``timeout`` is a runtime-operation budget in seconds, not a deadline for
        the entire print; individual protocol steps may have their own limits.
        Send/reply failures propagate. Returning successfully does not guarantee
        mechanical completion on protocols without a completion acknowledgement.
        The connection stays open.
        """
        await send_prepared_job(
            self._prepared,
            self._connection,
            job,
            timeout=timeout,
            reporter=self._reporter,
        )

    def print_capabilities(self) -> RuntimePrintCapabilities | None:
        """Return the capability snapshot prepared for this connection, without I/O.

        ``None`` means no additional session findings were provided, not that
        the printer lacks features. Pass this snapshot to ``PrinterProtocol``
        capability/pipeline queries; this method does not re-query the printer.
        """
        return self._prepared.capabilities

    def printer_device(self) -> "PrinterDevice":
        """Return the resolved immutable device description, without I/O.

        Use this instead of the discovery-time device for paper choices and job
        building: connection setup may have refined geometry or the variant.
        """
        return self._prepared.device

    def raster_page_job(
        self,
        raster_set: RasterSet,
        *,
        is_text: bool,
        settings: PrintSettings | None = None,
        page_index: int = 1,
        page_count: int = 1,
        page_flow: PageFlow = PageFlow.PAGED,
        image_pipeline: ImagePipelineConfig | None = None,
    ) -> ProtocolJob:
        """Build a page using this session's device/capabilities, without sending.

        ``settings=None`` creates default PrintSettings. Paper placement is
        applied to the supplied raster; this does not load or rasterize a file.
        Page indices are one-based. Use ``CONTINUOUS`` for fragments of a single
        media page, and ``PAGED`` for independent pages. Invalid geometry or
        format combinations raise ``ValueError``.
        """
        return _build_raster_page_job(
            self._prepared.device,
            raster_set,
            is_text=is_text,
            settings=settings,
            runtime_capabilities=self._prepared.capabilities,
            page_index=page_index,
            page_count=page_count,
            page_flow=page_flow,
            image_pipeline=image_pipeline,
        )

    def raster_job(
        self,
        raster_set: RasterSet,
        *,
        is_text: bool,
        settings: PrintSettings | None = None,
        image_pipeline: ImagePipelineConfig | None = None,
    ) -> ProtocolJob:
        """Build a complete single-page job without sending; see ``raster_page_job``.

        Uses this session's capabilities and default PrintSettings when omitted.
        Pass the result to ``send_job()`` to execute it.
        """
        page_job = self.raster_page_job(
            raster_set,
            is_text=is_text,
            settings=settings,
            page_index=1,
            page_count=1,
            image_pipeline=image_pipeline,
        )
        return self.raster_pages_job((page_job,))

    def raster_pages_job(
        self,
        page_jobs: Iterable[ProtocolJob],
    ) -> ProtocolJob:
        """Combine prepared page jobs in order, preserving steps, without sending.

        Pages must already use this device's recipe and correct page-flow/index
        metadata. This method neither renders nor re-encodes their payloads.
        """
        return _combine_raster_page_jobs(page_jobs)

    async def print_file(
        self,
        path: str,
        *,
        settings: PrintSettings | None = None,
        timeout: float = 1.0,
    ) -> None:
        """Load, render and send a supported image, PDF or text file.

        ``settings=None`` uses PrintSettings defaults. File/format/settings errors
        occur during building, before sending; transport/runtime errors propagate
        from ``send_job()``. Its timeout and completion semantics also apply here.
        The connection remains open for subsequent prints.
        """
        job = PrintJobBuilder(
            self._prepared.device,
            settings=settings,
            runtime_capabilities=self._prepared.capabilities,
            reporter=self._reporter,
        ).build_from_file(path)
        await self.send_job(job, timeout=timeout)

    async def print_text(
        self,
        text: str,
        *,
        settings: PrintSettings | None = None,
        timeout: float = 1.0,
    ) -> None:
        """Print text through ``print_file()`` using a temporary UTF-8 text file.

        The temporary file is removed on success or failure. Settings, timeout
        and completion semantics are the same as for ``print_file()``.
        """
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
                handle.write(text)
                temp_path = handle.name
            await self.print_file(temp_path, settings=settings, timeout=timeout)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    async def _paper_motion(self, action: str, *, timeout: float = 1.0) -> None:
        job = PrinterProtocol(self._prepared.device).build_paper_motion(action)
        await self.send_job(job, timeout=timeout)

    async def feed(self, *, timeout: float = 1.0) -> None:
        """Send one recipe-specific paper advance; no distance argument is implied.

        Check ``PrinterProtocol.supports_paper_motion("feed")`` before offering
        this action. Unsupported recipes may raise or do nothing. Send failures
        propagate; the timeout has the same meaning as in ``send_job()``.
        """
        await self._paper_motion("feed", timeout=timeout)

    async def retract(self, *, timeout: float = 1.0) -> None:
        """Send one recipe-specific paper retraction, when implemented.

        Check ``PrinterProtocol.supports_paper_motion("retract")`` first; there
        is no universal reverse-feed command. Unsupported recipes may raise or
        do nothing. Send failures and timeout semantics follow ``send_job()``.
        """
        await self._paper_motion("retract", timeout=timeout)

    async def disconnect(self) -> None:
        """Close the underlying transport connection."""
        await self._connection.disconnect()

    async def __aenter__(self) -> "ConnectedPrinter":
        """Return this connected printer for async context-manager use."""
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        """Disconnect when leaving an async context-manager block."""
        await self.disconnect()


async def connect_printer(
    device: "PrinterDevice",
    connector: "PrinterConnector",
    *,
    timeout: float = 1.0,
    reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    controller: RuntimeController | None = None,
) -> ConnectedPrinter:
    """Open a connection, prepare protocol runtime, and return its resolved device.

    This performs I/O, including any required handshake or capability queries.
    ``timeout`` is passed to runtime preparation, not ``connector.connect()``;
    connection establishment uses the connector's own policy. Preparation may
    refine the device's geometry, presets, variant or family before returning.
    An explicit ``controller`` must be a fresh bootstrap for this connection;
    omit it to use the catalog-selected runtime.

    On preparation failure or cancellation, close the connection and re-raise
    the original error. On success, the caller owns cleanup (use ``async with``).
    """
    connection = await connector.connect(device)
    try:
        prepared = await prepare_connection_runtime(
            device,
            connection,
            timeout=timeout,
            reporter=reporter,
            controller=controller,
        )
    except BaseException:
        try:
            await connection.disconnect()
        except Exception:
            pass
        raise
    return ConnectedPrinter(
        connection,
        prepared,
        reporter=reporter,
    )
