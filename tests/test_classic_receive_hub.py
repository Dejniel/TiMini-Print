from __future__ import annotations

import selectors
import socket
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from timiniprint.transport._stream_receive import StreamReceiveHub


class StreamReceiveHubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sock, self.peer = socket.socketpair()
        self.addCleanup(self.sock.close)
        self.addCleanup(self.peer.close)

    def test_selectable_socket_keeps_send_timeout_unchanged(self) -> None:
        for timeout in (None, 12.0):
            with self.subTest(timeout=timeout):
                self.sock.settimeout(timeout)
                hub = StreamReceiveHub(self.sock, poll_timeout=0.01)
                self.addCleanup(hub.stop)
                waiter = hub.register_passive(lambda data: data == b"hello")
                hub.start()

                self.assertEqual(self.sock.gettimeout(), timeout)
                self.peer.sendall(b"hello")
                self.assertEqual(hub.wait(waiter, timeout=1.0), b"hello")

                hub.stop()
                self.assertEqual(self.sock.gettimeout(), timeout)
                self.sock.sendall(b"still writable")
                self.assertEqual(self.peer.recv(4096), b"still writable")

    def test_send_can_outlast_receive_poll_when_peer_buffer_is_full(self) -> None:
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        # Fill both ends' OS buffers before starting the hub. TCP socket pairs
        # may absorb more than SO_SNDBUF suggests, especially on Windows.
        self.sock.setblocking(False)
        queued = 0
        fill_deadline = time.monotonic() + 5.0
        with selectors.DefaultSelector() as writable:
            writable.register(self.sock, selectors.EVENT_WRITE)
            while writable.select(timeout=0.2):
                self.assertLess(time.monotonic(), fill_deadline, "could not fill socket buffers")
                try:
                    queued += self.sock.send(b"x" * 65536)
                except BlockingIOError:
                    pass
        self.sock.settimeout(5.0)
        self.peer.settimeout(2.0)
        hub = StreamReceiveHub(self.sock, poll_timeout=0.01)
        self.addCleanup(hub.stop)
        hub.start()
        payload = b"x" * (1024 * 1024)
        started = threading.Event()
        finished = threading.Event()
        errors = []

        def send() -> None:
            started.set()
            try:
                self.sock.sendall(payload)
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        writer = threading.Thread(target=send, daemon=True)
        writer.start()
        try:
            self.assertTrue(started.wait(1.0))
            # Deliberately stop draining longer than the old receive timeout:
            # a full peer buffer must not make the writer fail after 10 ms.
            self.assertFalse(finished.wait(0.2), repr(errors))
            received = bytearray()
            while len(received) < queued + len(payload):
                chunk = self.peer.recv(65536)
                self.assertTrue(chunk, "writer closed before sending the full payload")
                received.extend(chunk)
            self.assertTrue(finished.wait(1.0))
            self.assertEqual(errors, [])
            self.assertEqual(received, b"x" * (queued + len(payload)))
            self.assertEqual(self.sock.gettimeout(), 5.0)
        finally:
            self.peer.close()
            writer.join(timeout=3.0)
        self.assertFalse(writer.is_alive())

    def test_hub_can_wait_for_multiple_chunks(self) -> None:
        first_chunk = threading.Event()
        chunks = []

        def receive(data: bytes) -> None:
            chunks.append(data)
            first_chunk.set()

        hub = StreamReceiveHub(self.sock, listener=receive, poll_timeout=0.01)
        self.addCleanup(hub.stop)
        waiter = hub.register_passive(lambda data: data == b"alphabeta")
        hub.start()

        self.peer.sendall(b"alpha")
        self.assertTrue(first_chunk.wait(1.0))
        self.peer.sendall(b"beta")
        self.assertEqual(hub.wait(waiter, timeout=1.0), b"alphabeta")
        hub.stop()
        self.assertEqual(chunks, [b"alpha", b"beta"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor limit regression")
    def test_high_numbered_socket_keeps_send_timeout_and_receives(self) -> None:
        import fcntl
        import resource

        if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 1100:
            self.skipTest("descriptor limit is too low for a descriptor above FD_SETSIZE")
        high_sock = socket.socket(fileno=fcntl.fcntl(self.sock, fcntl.F_DUPFD, 1100))
        self.addCleanup(high_sock.close)
        high_sock.settimeout(12.0)
        hub = StreamReceiveHub(high_sock, poll_timeout=0.01)
        self.addCleanup(hub.stop)
        waiter = hub.register_passive(lambda data: data == b"reply")
        hub.start()

        self.assertEqual(high_sock.gettimeout(), 12.0)
        self.peer.sendall(b"reply")
        self.assertEqual(hub.wait(waiter, timeout=1.0), b"reply")
        hub.stop()
        self.assertEqual(high_sock.gettimeout(), 12.0)

    def test_send_only_adapter_does_not_start_receiver_or_change_timeout(self) -> None:
        sock = Mock(spec=["sendall", "settimeout", "close"])
        hub = StreamReceiveHub(sock)
        with patch(
            "timiniprint.transport._stream_receive.selectors.DefaultSelector"
        ) as factory:
            hub.start()
            hub.stop()

        factory.assert_not_called()
        sock.settimeout.assert_not_called()
        sock.close.assert_not_called()

    def test_registration_failure_closes_selector_without_changing_timeout(self) -> None:
        self.sock.settimeout(12.0)
        hub = StreamReceiveHub(self.sock)
        selector = Mock(spec=selectors.BaseSelector)
        selector.register.side_effect = ValueError("cannot monitor socket")
        with patch(
            "timiniprint.transport._stream_receive.selectors.DefaultSelector",
            return_value=selector,
        ):
            with self.assertRaisesRegex(ValueError, "cannot monitor socket"):
                hub.start()

        selector.close.assert_called_once_with()
        self.assertEqual(self.sock.gettimeout(), 12.0)

    def test_thread_start_failure_closes_selector(self) -> None:
        selector = Mock(wraps=selectors.DefaultSelector())
        self.addCleanup(selector.close)
        hub = StreamReceiveHub(self.sock)
        with patch(
            "timiniprint.transport._stream_receive.selectors.DefaultSelector",
            return_value=selector,
        ), patch("threading.Thread.start", side_effect=RuntimeError("cannot start thread")):
            with self.assertRaisesRegex(RuntimeError, "cannot start thread"):
                hub.start()

        selector.close.assert_called_once_with()
        self.assertIsNone(self.sock.gettimeout())

    def test_stop_idle_reader_closes_selector_and_wakes_waiters(self) -> None:
        selector = Mock(wraps=selectors.DefaultSelector())
        self.addCleanup(selector.close)
        hub = StreamReceiveHub(self.sock, poll_timeout=0.01)
        self.addCleanup(hub.stop)
        waiter = hub.register_passive(lambda data: data == b"reply")
        with patch(
            "timiniprint.transport._stream_receive.selectors.DefaultSelector",
            return_value=selector,
        ):
            hub.start()
            hub.start()
        hub.stop()
        hub.stop()

        selector.register.assert_called_once_with(self.sock, selectors.EVENT_READ)
        selector.close.assert_called_once_with()
        self.assertTrue(waiter.event.is_set())
        self.assertIsNone(hub.wait(waiter, timeout=0.0))
        self.assertIsNone(self.sock.gettimeout())

    def test_peer_eof_closes_selector_and_wakes_waiters(self) -> None:
        selector = Mock(wraps=selectors.DefaultSelector())
        self.addCleanup(selector.close)
        hub = StreamReceiveHub(self.sock, poll_timeout=0.01)
        self.addCleanup(hub.stop)
        waiter = hub.register_passive(lambda data: data == b"reply")
        with patch(
            "timiniprint.transport._stream_receive.selectors.DefaultSelector",
            return_value=selector,
        ):
            hub.start()
        self.peer.shutdown(socket.SHUT_WR)

        self.assertTrue(waiter.event.wait(1.0))
        with self.assertRaisesRegex(ConnectionError, "peer closed"):
            hub.wait(waiter, timeout=0.0)
        selector.close.assert_called_once_with()

    def test_passive_cursor_never_moves_backwards(self) -> None:
        hub = StreamReceiveHub(self.sock)
        earlier = hub.register_passive(lambda data: data == b"A")
        later = hub.register_passive(lambda data: data == b"AB")
        hub.feed(b"A")
        hub.feed(b"B")
        self.assertEqual(hub.wait(later, timeout=0, claim_passive=True), b"AB")
        self.assertEqual(hub.wait(earlier, timeout=0, claim_passive=True), b"A")
        next_reply = hub.register_passive(bool)
        self.assertIsNone(hub.wait(next_reply, timeout=0, claim_passive=True))

    def test_terminal_failure_is_not_overwritten_by_later_bytes(self) -> None:
        errors = []
        hub = StreamReceiveHub(self.sock, on_error=errors.append)
        waiter = hub.register_passive(bool)
        error = OSError("receiver failed")
        hub.fail(error)
        hub.fail(OSError("another failure"))
        hub.feed(b"late ACK")
        with self.assertRaisesRegex(OSError, "receiver failed"):
            hub.wait(waiter, timeout=0)
        self.assertEqual(errors, [error])

    def test_receiver_failure_during_start_prevents_the_query_write(self) -> None:
        class FailedSource:
            def start_receive(self, on_data, on_error):
                on_error(OSError("receiver could not start"))

            def stop_receive(self):
                pass

        hub = StreamReceiveHub(FailedSource())
        self.addCleanup(hub.stop)
        send = Mock()
        with self.assertRaisesRegex(OSError, "receiver could not start"):
            hub.query(b"must not be sent", send, timeout=0.2)
        send.assert_not_called()

    def test_queued_query_expires_without_being_sent_late(self) -> None:
        hub = StreamReceiveHub(self.sock)
        started = threading.Event()
        sent, replies, errors = [], [], []

        def send(packet, budget):
            sent.append(packet)
            started.set()

        def first():
            try:
                replies.append(hub.query(b"Q1", send, timeout=1, reply_complete=bool))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=first, daemon=True)
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            with self.assertRaisesRegex(TimeoutError, "another stream query"):
                hub.query(b"Q2", send, timeout=0.02, reply_complete=bool)
            self.assertEqual(sent, [b"Q1"])
            hub.feed(b"R1")
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(replies, [b"R1"])
            self.assertEqual(errors, [])
        finally:
            hub.stop()
            worker.join(1)


if __name__ == "__main__":
    unittest.main()
