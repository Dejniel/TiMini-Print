"""Expected printer conditions exposed by the connected printing API."""

from ..protocol.status import PrinterStatusCode


class PrinterNotReadyError(Exception):
    """A reported printer condition prevented a successful operation.

    ``reasons`` contains stable codes for UI decisions/localization; ``detail``
    (also ``str(error)``) retains family-specific diagnostic information.
    This is not a connection failure and does not imply that nothing printed.
    Do not automatically retry the job: paper or copies may already be printed.
    """

    def __init__(
        self, detail: str, reason: PrinterStatusCode, *other_reasons: PrinterStatusCode,
    ) -> None:
        self.detail = detail
        self.reasons = tuple(dict.fromkeys(PrinterStatusCode(code) for code in (reason, *other_reasons)))
        super().__init__(detail, *self.reasons)

    def __str__(self) -> str:
        return self.detail
