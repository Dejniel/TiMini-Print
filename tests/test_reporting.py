from __future__ import annotations

import io
import queue
import unittest
from unittest.mock import Mock

from tests.helpers import build_capture_reporter
from timiniprint import reporting
from timiniprint.reporting import ReportLevel


class ReportingTests(unittest.TestCase):
    def test_message_catalog_and_summary(self) -> None:
        self.assertEqual(reporting.MessageCatalog.resolve(ReportLevel.STATUS, reporting.STATUS_CONNECT_DONE), "Connected")
        self.assertEqual(reporting.MessageCatalog.resolve(ReportLevel.WARNING, reporting.WARNING_DEPENDENCY), "Missing dependency")
        self.assertEqual(reporting.MessageCatalog.resolve(ReportLevel.ERROR, reporting.ERROR_SCAN_FAILED), "Scan failed")
        self.assertIsNone(reporting.MessageCatalog.resolve(ReportLevel.DEBUG, reporting.ERROR_SCAN_FAILED))
        self.assertIsNone(reporting.MessageCatalog.resolve(ReportLevel.TRACE, reporting.ERROR_SCAN_FAILED))
        self.assertEqual(reporting.summarize_detail("Ala ma kota. I psa"), "Ala ma kota")
        self.assertTrue(reporting.summarize_detail("x" * 200).endswith("..."))

    def test_default_stderr_threshold_and_prefixes(self) -> None:
        out = io.StringIO()
        reporter = reporting.Reporter([reporting.StderrSink(stream=out)])
        reporter.trace(detail="private raster")
        reporter.debug(detail="job summary")
        reporter.status(short="printing")
        reporter.warning(short="w")
        reporter.error(short="e")
        self.assertEqual(out.getvalue(), "Warning: w\nError: e\n")

    def test_stderr_threshold_can_be_updated(self) -> None:
        out = io.StringIO()
        sink = reporting.StderrSink(stream=out)
        reporter = reporting.Reporter([sink])
        reporter.debug(detail="d1")
        sink.min_level = ReportLevel.DEBUG
        reporter.debug(detail="d2")
        self.assertEqual(out.getvalue(), "d2\n")

    def test_stderr_thresholds_preserve_diagnostic_order_but_not_progress(self) -> None:
        for threshold in ReportLevel:
            with self.subTest(threshold=threshold):
                out = io.StringIO()
                reporter = reporting.Reporter(
                    [reporting.StderrSink(stream=out, min_level=threshold)],
                    min_level=ReportLevel.TRACE,
                )
                for level in ReportLevel:
                    getattr(reporter, level.name.lower())(short=level.name)
                prefixes = {ReportLevel.WARNING: "Warning: ", ReportLevel.ERROR: "Error: "}
                expected = "".join(
                    prefixes.get(level, "") + level.name + "\n"
                    for level in ReportLevel if level >= threshold and level != ReportLevel.STATUS
                )
                self.assertEqual(out.getvalue(), expected)

    def test_queue_status_sink_mapping(self) -> None:
        q = queue.Queue()
        sink = reporting.QueueStatusSink(q)
        reporter = reporting.Reporter([sink], min_level=ReportLevel.TRACE)
        reporter.trace(short="raw bytes")
        reporter.debug(short="summary")
        reporter.status(short="ok")
        reporter.warning(short="warn")
        reporter.error(short="err")
        items = [q.get_nowait(), q.get_nowait(), q.get_nowait()]
        self.assertEqual(items[0], ("status", "ok"))
        self.assertEqual(items[1], ("status", "Warning: warn"))
        self.assertEqual(items[2], ("error", "err"))
        self.assertTrue(q.empty())

    def test_queue_can_hide_warnings_without_losing_progress_or_errors(self) -> None:
        q = queue.Queue()
        reporter = reporting.Reporter([reporting.QueueStatusSink(q, show_warnings=False)])
        reporter.status(reporting.STATUS_CONNECT_DONE)
        reporter.warning(short="warn")
        reporter.error(short="err")
        self.assertEqual(q.get_nowait(), ("status", "Connected"))
        self.assertEqual(q.get_nowait(), ("error", "err"))
        self.assertTrue(q.empty())

    def test_levels_are_ordered(self) -> None:
        self.assertEqual(list(ReportLevel), [
            ReportLevel.TRACE, ReportLevel.DEBUG, ReportLevel.STATUS,
            ReportLevel.WARNING, ReportLevel.ERROR,
        ])
        self.assertEqual(sorted(reversed(list(ReportLevel))), list(ReportLevel))

    def test_default_reporter_filters_trace_before_custom_sink(self) -> None:
        reporter, sink = build_capture_reporter()
        reporter.trace(detail="private raster", path="private-file.png")
        reporter.debug(short="summary")
        reporter.status(short="printing")
        reporter.warning(short="warning")
        reporter.error(short="error")
        self.assertEqual([message.level for message in sink.messages], [
            ReportLevel.DEBUG, ReportLevel.STATUS, ReportLevel.WARNING, ReportLevel.ERROR,
        ])
        self.assertTrue(all(isinstance(message.level, ReportLevel) for message in sink.messages))

    def test_reporter_thresholds_apply_before_custom_sink(self) -> None:
        for threshold in ReportLevel:
            with self.subTest(threshold=threshold):
                reporter, sink = build_capture_reporter(min_level=threshold)
                for level in ReportLevel:
                    getattr(reporter, level.name.lower())(short=level.name)
                self.assertEqual([message.level for message in sink.messages],
                                 [level for level in ReportLevel if level >= threshold])

    def test_filtered_messages_are_not_formatted(self) -> None:
        sink = Mock(spec=reporting.ReportSink)
        catalog = Mock(spec=reporting.MessageCatalog)
        error = Mock(spec=Exception)
        error.__str__ = Mock(side_effect=AssertionError("filtered exception was formatted"))
        reporter = reporting.Reporter([sink], catalog=catalog)
        reporter.trace("raw", exc=error)
        sink.emit.assert_not_called()
        catalog.resolve.assert_not_called()

    def test_reporter_threshold_can_enable_and_disable_trace(self) -> None:
        reporter, sink = build_capture_reporter()
        reporter.trace(short="discarded")
        reporter.min_level = ReportLevel.TRACE
        reporter.trace(short="explicitly enabled")
        reporter.min_level = ReportLevel.DEBUG
        reporter.trace(short="discarded again")
        message, = sink.messages
        self.assertEqual(message.level, ReportLevel.TRACE)
        self.assertEqual(message.short, "explicitly enabled")

    def test_trace_sink_does_not_override_reporter_threshold(self) -> None:
        out = io.StringIO()
        reporter = reporting.Reporter([
            reporting.StderrSink(stream=out, min_level=ReportLevel.TRACE),
        ])
        reporter.trace(detail="private raster")
        reporter.debug(detail="summary")
        self.assertEqual(out.getvalue(), "summary\n")

    def test_reporter_emit_paths(self) -> None:
        sink = []

        class _S(reporting.ReportSink):
            def emit(self, msg):
                sink.append(msg)

        rep = reporting.Reporter([_S()])
        rep.error(key=reporting.ERROR_SCAN_FAILED)
        rep.warning(short="custom")
        rep.debug(detail="detail only")
        rep.error(exc=RuntimeError("boom"))
        self.assertEqual(len(sink), 4)
        self.assertEqual(sink[1].short, "custom")
        self.assertEqual(sink[2].short, "detail only")
        self.assertIn("boom", sink[3].detail)


if __name__ == "__main__":
    unittest.main()
