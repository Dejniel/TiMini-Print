import queue

from timiniprint import reporting
from timiniprint.printing.runtime.session import RuntimeConnectionSession


def test_runtime_status_uses_the_existing_reporter_queue():
    messages = queue.Queue()
    reporter = reporting.Reporter([reporting.QueueStatusSink(messages)])
    session = RuntimeConnectionSession(object(), reporter=reporter)
    session.report_status("Waiting for paper")
    assert messages.get_nowait() == ("status", "Waiting for paper")
