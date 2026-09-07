from __future__ import annotations

import socket
import threading
import time
import unittest

from timiniprint.transport.bluetooth.classic_receive import ClassicReceiveHub


class ClassicReceiveHubTests(unittest.TestCase):
    """Regression tests for ClassicReceiveHub behavior."""

    def test_selectable_socket_keeps_send_timeout_unchanged(self) -> None:
        """A real Bluetooth socket should be polled with select(), not settimeout().

        Using settimeout() to poll recv() would also shorten sendall() and
        cause spurious timeouts when the printer's buffer is full.
        """
        read_sock, write_sock = socket.socketpair()
        self.addCleanup(read_sock.close)
        self.addCleanup(write_sock.close)

        original_timeout = read_sock.gettimeout()
        hub = ClassicReceiveHub(read_sock)
        hub.start()
        self.addCleanup(hub.stop)

        self.assertEqual(read_sock.gettimeout(), original_timeout)
        self.assertIsNone(read_sock.gettimeout())

        write_sock.sendall(b"hello")
        # Give the reader thread time to poll and receive.
        time.sleep(0.05)

        waiter = hub.register_passive(lambda data: data == b"hello")
        result = hub.wait(waiter, timeout=0.5)
        self.assertEqual(result, b"hello")

    def test_non_selectable_socket_falls_back_to_settimeout(self) -> None:
        """Mock sockets without fileno() should still work with the legacy path."""

        class _FakeSocket:
            def __init__(self) -> None:
                self._replies = [b"ok"]
                self.timeout: float | None = None

            def gettimeout(self) -> float | None:
                return self.timeout

            def settimeout(self, timeout: float | None) -> None:
                self.timeout = timeout

            def recv(self, _size: int) -> bytes:
                if self._replies:
                    return self._replies.pop(0)
                raise TimeoutError()

            def fileno(self) -> int:
                raise TypeError("fake socket has no fd")

        sock = _FakeSocket()
        hub = ClassicReceiveHub(sock, poll_timeout=0.01)
        hub.start()
        self.addCleanup(hub.stop)

        # Fallback path sets the poll timeout so recv() can time out.
        self.assertEqual(sock.timeout, 0.01)

    def test_hub_can_wait_for_multiple_chunks(self) -> None:
        """Data split across multiple writes is buffered and matched correctly."""
        read_sock, write_sock = socket.socketpair()
        self.addCleanup(read_sock.close)
        self.addCleanup(write_sock.close)

        hub = ClassicReceiveHub(read_sock)
        hub.start()
        self.addCleanup(hub.stop)

        write_sock.sendall(b"alpha")
        write_sock.sendall(b"beta")
        time.sleep(0.05)

        waiter = hub.register_passive(lambda data: data == b"alphabeta")
        result = hub.wait(waiter, timeout=0.5)
        self.assertEqual(result, b"alphabeta")


if __name__ == "__main__":
    unittest.main()
