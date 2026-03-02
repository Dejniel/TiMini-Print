from __future__ import annotations

import io
import queue
import unittest

from timiniprint import reporting


class ReportingTests(unittest.TestCase):
    def test_message_catalog_and_summary(self) -> None:
        self.assertEqual(reporting.MessageCatalog.resolve("status", reporting.STATUS_CONNECT_DONE), "Connected")
        self.assertIsNone(reporting.MessageCatalog.resolve("debug", "missing"))
        self.assertEqual(reporting.summarize_detail("Ala ma kota. I psa"), "Ala ma kota")
        self.assertTrue(reporting.summarize_detail("x" * 200).endswith("..."))

    def test_stderr_sink_levels_and_prefixes(self) -> None:
        out = io.StringIO()
        sink = reporting.StderrSink(stream=out, levels={"warning", "error"})
        sink.emit(reporting.ReportMessage(level="warning", key=None, short="w"))
        sink.emit(reporting.ReportMessage(level="error", key=None, short="e"))
        sink.emit(reporting.ReportMessage(level="status", key=None, short="s"))
        text = out.getvalue()
        self.assertIn("Warning: w", text)
        self.assertIn("Error: e", text)
        self.assertNotIn("s", text)

    def test_queue_status_sink_mapping(self) -> None:
        q = queue.Queue()
        sink = reporting.QueueStatusSink(q)
        sink.emit(reporting.ReportMessage(level="status", key=None, short="ok"))
        sink.emit(reporting.ReportMessage(level="warning", key=None, short="warn"))
        sink.emit(reporting.ReportMessage(level="error", key=None, short="err"))
        items = [q.get_nowait(), q.get_nowait(), q.get_nowait()]
        self.assertEqual(items[0], ("status", "ok"))
        self.assertEqual(items[1], ("status", "Warning: warn"))
        self.assertEqual(items[2], ("error", "err"))

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
