from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from timiniprint import reporting
from timiniprint.transport._stream_session import StreamRuntimeSession


class StreamSessionDispatchTests(unittest.TestCase):
    def test_queued_notification_does_not_reach_detached_or_replacement_controller(self):
        queued = threading.Event()
        callbacks = []

        def enqueue(callback):
            callbacks.append(callback)
            queued.set()

        loop = SimpleNamespace(call_soon_threadsafe=enqueue)
        session = StreamRuntimeSession(reporting.DUMMY_REPORTER, label="test")
        old_received, new_received, errors = [], [], []
        old = SimpleNamespace(handle_notification=lambda _, data: old_received.append(data))
        new = SimpleNamespace(handle_notification=lambda _, data: new_received.append(data))
        current = old
        session.bind(loop, None, lambda: False)

        def receive():
            try:
                session.dispatch(lambda: current, b"old bytes")
            except Exception as exc:
                errors.append(exc)

        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        try:
            self.assertTrue(queued.wait(1))
            current = new
            session.detach()
            session.bind(loop, None, lambda: False)
            callbacks.pop()()
            reader.join(1)
            self.assertFalse(reader.is_alive())
            self.assertEqual(old_received, [])
            self.assertEqual(new_received, [])
            self.assertEqual(errors, [])
        finally:
            for callback in callbacks:
                callback()
            reader.join(1)
