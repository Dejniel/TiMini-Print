from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
import tempfile
from typing import TYPE_CHECKING

from .. import reporting
from ..protocol import PrinterProtocol, ProtocolJob
from ..protocol.runtime import RuntimePrintCapabilities
from ..protocol.types import ImagePipelineConfig
from ..raster import RasterSet
from .builder import PrintJobBuilder
from .raster_job import build_raster_page_job as _build_raster_page_job
from .raster_job import combine_raster_page_jobs as _combine_raster_page_jobs
from .runtime.base import PreparedRuntimeContext
from .runtime.prepare import prepare_connection_runtime
from .send import send_prepared_job
from .settings import PrintSettings

if TYPE_CHECKING:
    from ..devices import PrinterDevice
    from ..transport.base import PrinterConnection, PrinterConnector


@dataclass(frozen=True, init=False)
class ConnectedPrinter:
    """Resolved printer with an active transport connection and prepared runtime."""

    _device: "PrinterDevice"
    _connection: "PrinterConnection"
    _runtime_context: PreparedRuntimeContext
    _reporter: reporting.Reporter = reporting.DUMMY_REPORTER

    def __init__(
        self,
        device: "PrinterDevice",
        connection: "PrinterConnection",
        runtime_context: PreparedRuntimeContext,
        *,
        reporter: reporting.Reporter = reporting.DUMMY_REPORTER,
    ) -> None:
        """Wrap a connection whose runtime has already been prepared by the caller."""
        object.__setattr__(self, "_device", device)
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_runtime_context", runtime_context)
        object.__setattr__(self, "_reporter", reporter)

    async def send_job(self, job: ProtocolJob, *, timeout: float = 1.0) -> None:
        """Send an already-built protocol job through this prepared connection."""
        await send_prepared_job(
            self._device,
            self._connection,
            job,
            timeout=timeout,
            reporter=self._reporter,
        )

    def raster_capabilities(self) -> RuntimePrintCapabilities | None:
        """Return live-session capabilities that affect raster rendering and encoding."""
        return self._runtime_context.capabilities

    def raster_page_job(
        self,
        raster_set: RasterSet,
        *,
        is_text: bool,
        settings: PrintSettings | None = None,
        page_index: int = 1,
        page_count: int = 1,
        image_pipeline: ImagePipelineConfig | None = None,
    ) -> ProtocolJob:
        """Build one printable protocol page from an already rendered raster."""
        return _build_raster_page_job(
            self._device,
            raster_set,
            is_text=is_text,
            settings=settings,
            runtime_context=self._runtime_context,
            page_index=page_index,
            page_count=page_count,
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
        """Build a complete one-page raster protocol job."""
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
        """Build one protocol job from already-built raster page jobs."""
        return _combine_raster_page_jobs(
            self._device,
            page_jobs,
            runtime_context=self._runtime_context,
        )

    async def print_file(
        self,
        path: str,
        *,
        settings: PrintSettings | None = None,
        timeout: float = 1.0,
    ) -> None:
        """Build and send a print job for a supported document/image file."""
        job = PrintJobBuilder(
            self._device,
            settings=settings,
            runtime_context=self._runtime_context,
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
        """Render and print raw text using the same pipeline as a temporary text file."""
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
        job = PrinterProtocol(self._device).build_paper_motion(action)
        await self.send_job(job, timeout=timeout)

    async def feed(self, *, timeout: float = 1.0) -> None:
        """Advance paper using the connected printer's protocol-specific command."""
        await self._paper_motion("feed", timeout=timeout)

    async def retract(self, *, timeout: float = 1.0) -> None:
        """Retract paper when the connected printer protocol supports it."""
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
) -> ConnectedPrinter:
    connection = await connector.connect(device)
    try:
        runtime_context = await prepare_connection_runtime(
            device,
            connection,
            timeout=timeout,
            reporter=reporter,
        )
    except Exception:
        try:
            await connection.disconnect()
        except Exception:
            pass
        raise
    return ConnectedPrinter(
        device,
        connection,
        runtime_context,
        reporter=reporter,
    )
