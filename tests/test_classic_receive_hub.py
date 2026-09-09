from __future__ import annotations

import selectors
import socket
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from timiniprint.transport.bluetooth.classic_receive import ClassicReceiveHub


class ClassicReceiveHubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sock, self.peer = socket.socketpair()
        self.addCleanup(self.sock.close)
        self.addCleanup(self.peer.close)

    def test_selectable_socket_keeps_send_timeout_unchanged(self) -> None:
        for timeout in (None, 12.0):
            with self.subTest(timeout=timeout):
                self.sock.settimeout(timeout)
                hub = ClassicReceiveHub(self.sock, poll_timeout=0.01)
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
        self.sock.settimeout(2.0)
        self.peer.settimeout(2.0)
        hub = ClassicReceiveHub(self.sock, poll_timeout=0.01)
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
            while len(received) < len(payload):
                chunk = self.peer.recv(65536)
                self.assertTrue(chunk, "writer closed before sending the full payload")
                received.extend(chunk)
            self.assertTrue(finished.wait(1.0))
            self.assertEqual(errors, [])
            self.assertEqual(received, payload)
            self.assertEqual(self.sock.gettimeout(), 2.0)
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

        hub = ClassicReceiveHub(self.sock, listener=receive, poll_timeout=0.01)
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
        hub = ClassicReceiveHub(high_sock, poll_timeout=0.01)
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
        hub = ClassicReceiveHub(sock)
        with patch(
            "timiniprint.transport.bluetooth.classic_receive.selectors.DefaultSelector"
        ) as factory:
            hub.start()
            hub.stop()

        factory.assert_not_called()
        sock.settimeout.assert_not_called()
        sock.close.assert_not_called()

    def test_registration_failure_closes_selector_without_changing_timeout(self) -> None:
        self.sock.settimeout(12.0)
        hub = ClassicReceiveHub(self.sock)
        selector = Mock(spec=selectors.BaseSelector)
        selector.register.side_effect = ValueError("cannot monitor socket")
        with patch(
            "timiniprint.transport.bluetooth.classic_receive.selectors.DefaultSelector",
            return_value=selector,
        ):
            with self.assertRaisesRegex(ValueError, "cannot monitor socket"):
                hub.start()

        selector.close.assert_called_once_with()
        self.assertEqual(self.sock.gettimeout(), 12.0)

    def test_thread_start_failure_closes_selector(self) -> None:
        selector = Mock(wraps=selectors.DefaultSelector())
        self.addCleanup(selector.close)
        hub = ClassicReceiveHub(self.sock)
        with patch(
            "timiniprint.transport.bluetooth.classic_receive.selectors.DefaultSelector",
            return_value=selector,
        ), patch("threading.Thread.start", side_effect=RuntimeError("cannot start thread")):
            with self.assertRaisesRegex(RuntimeError, "cannot start thread"):
                hub.start()

        selector.close.assert_called_once_with()
        self.assertIsNone(self.sock.gettimeout())

    def test_stop_idle_reader_closes_selector_and_wakes_waiters(self) -> None:
        selector = Mock(wraps=selectors.DefaultSelector())
        self.addCleanup(selector.close)
        hub = ClassicReceiveHub(self.sock, poll_timeout=0.01)
        self.addCleanup(hub.stop)
        waiter = hub.register_passive(lambda data: data == b"reply")
        with patch(
            "timiniprint.transport.bluetooth.classic_receive.selectors.DefaultSelector",
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
        hub = ClassicReceiveHub(self.sock, poll_timeout=0.01)
        self.addCleanup(hub.stop)
        waiter = hub.register_passive(lambda data: data == b"reply")
        with patch(
            "timiniprint.transport.bluetooth.classic_receive.selectors.DefaultSelector",
            return_value=selector,
        ):
            hub.start()
        self.peer.shutdown(socket.SHUT_WR)

        self.assertTrue(waiter.event.wait(1.0))
        self.assertIsNone(hub.wait(waiter, timeout=0.0))
        selector.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
