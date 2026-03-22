from __future__ import annotations

import types
import unittest
from unittest.mock import patch

from timiniprint.transport.bluetooth.adapters.linux_adapter import (
    _LINUX_AF_BLUETOOTH,
    _LINUX_BTPROTO_RFCOMM,
    _LinuxClassicAdapter,
)


class LinuxClassicAdapterTests(unittest.TestCase):
    def test_create_socket_uses_numeric_linux_fallback_when_constants_missing(self) -> None:
        created = []

        def _socket_factory(family, sock_type, proto):
            created.append((family, sock_type, proto))
            return object()

        fake_socket = types.SimpleNamespace(
            SOCK_STREAM=1,
            socket=_socket_factory,
        )

        adapter = _LinuxClassicAdapter()
        with patch("timiniprint.transport.bluetooth.adapters.linux_adapter.socket", fake_socket):
            sock = adapter.create_socket()

        self.assertIsNotNone(sock)
        self.assertEqual(created, [(_LINUX_AF_BLUETOOTH, 1, _LINUX_BTPROTO_RFCOMM)])

    def test_create_socket_wraps_runtime_without_rfcomm_support(self) -> None:
        def _socket_factory(_family, _sock_type, _proto):
            raise OSError("unsupported")

        fake_socket = types.SimpleNamespace(
            SOCK_STREAM=1,
            socket=_socket_factory,
        )

        adapter = _LinuxClassicAdapter()
        with patch("timiniprint.transport.bluetooth.adapters.linux_adapter.socket", fake_socket):
            with self.assertRaisesRegex(RuntimeError, "RFCOMM sockets are not supported by this Python runtime"):
                adapter.create_socket()


if __name__ == "__main__":
    unittest.main()
