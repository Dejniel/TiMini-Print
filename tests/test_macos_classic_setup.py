"""Native setup budgets exercised without IOBluetooth hardware."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from timiniprint.transport.bluetooth.adapters import macos_iobluetooth as native
from timiniprint.transport.bluetooth.adapters.macos_adapter import _MacClassicAdapter


class MacClassicSetupTests(unittest.TestCase):
    def test_adapter_forwards_the_remaining_sdp_budget(self):
        adapter = _MacClassicAdapter()
        adapter._backend = SimpleNamespace(resolve_rfcomm_channels=Mock(return_value=[7]))
        self.assertEqual(adapter.resolve_rfcomm_channels("AA", timeout=0.3), [7])
        adapter._backend.resolve_rfcomm_channels.assert_called_once_with("AA", timeout=0.3)

    def test_explicit_sdp_target_does_not_start_a_global_inquiry(self):
        backend = native._MacClassicBackend()
        device = object()
        backend.get_device = Mock(return_value=device)
        with patch.object(native, "_find_spp_channels", return_value=[7]):
            self.assertEqual(backend.resolve_rfcomm_channels("AA", timeout=0.3), [7])
        backend.get_device.assert_called_once_with("AA", allow_discovery=False, timeout=0.3)

    def test_native_sdp_cannot_restart_an_expired_budget(self):
        backend = native._MacClassicBackend()
        backend.get_device = Mock(return_value=object())
        times = iter((10.0, 10.01, 10.31))
        with patch.object(native, "time", SimpleNamespace(monotonic=lambda: next(times))), \
             patch.object(native, "_find_spp_channels", return_value=[7]):
            with self.assertRaisesRegex(TimeoutError, "SDP lookup exceeded"):
                backend.resolve_rfcomm_channels("AA", timeout=0.3)

    def test_native_channel_handle_is_retained_for_cleanup_after_deadline(self):
        backend = SimpleNamespace(get_device=Mock(return_value=object()))
        sock = native._MacClassicSocket(backend)
        sock.settimeout(0.3)
        channel = object()
        times = iter((10.0, 10.01, 10.31))
        with patch.object(native, "time", SimpleNamespace(monotonic=lambda: next(times))), \
             patch.object(native, "_open_rfcomm_channel", return_value=channel):
            with self.assertRaisesRegex(TimeoutError, "channel open exceeded"):
                sock._connect_native(("AA", 7))
        self.assertIs(sock._channel, channel)
        self.assertIsNotNone(sock._device)
