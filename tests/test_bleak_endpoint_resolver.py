from __future__ import annotations

import unittest

from timiniprint.transport.bluetooth.adapters import bleak_adapter


class _FakeCharacteristic:
    def __init__(self, uuid: str, properties):
        self.uuid = uuid
        self.properties = properties


class _FakeService:
    def __init__(self, uuid: str, characteristics):
        self.uuid = uuid
        self.characteristics = characteristics


class BleakEndpointResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = bleak_adapter._BleWriteEndpointResolver()

    def test_prefers_ae01_over_generic_write_on_ae30_service(self) -> None:
        services = [
            _FakeService(
                "0000ae30-0000-1000-8000-00805f9b34fb",
                [
                    _FakeCharacteristic("0000ae10-0000-1000-8000-00805f9b34fb", ["write"]),
                    _FakeCharacteristic(
                        "0000ae01-0000-1000-8000-00805f9b34fb",
                        ["write-without-response"],
                    ),
                ],
            )
        ]
        selection = self.resolver.resolve(services)
        self.assertIsNotNone(selection)
        self.assertEqual(selection.char_uuid, "0000ae01-0000-1000-8000-00805f9b34fb")
        self.assertEqual(selection.strategy, "preferred_uuid")
        self.assertFalse(selection.response_preference)

    def test_preferred_uuid_uses_write_without_response_mode(self) -> None:
        response = self.resolver.resolve_response_mode(
            ["write", "write-without-response"],
            "preferred_uuid",
            True,
        )
        self.assertFalse(response)

    def test_generic_fallback_prefers_wnr_and_is_deterministic(self) -> None:
        services = [
            _FakeService(
                "12345678-0000-1000-8000-00805f9b34fb",
                [
                    _FakeCharacteristic("12345678-0000-1000-8000-00805f9b34ff", ["write-without-response"]),
                    _FakeCharacteristic("12345678-0000-1000-8000-00805f9b34aa", ["write-without-response"]),
                ],
            )
        ]
        selection = self.resolver.resolve(services)
        self.assertIsNotNone(selection)
        self.assertEqual(selection.char_uuid, "12345678-0000-1000-8000-00805f9b34aa")
        self.assertEqual(selection.strategy, "generic_fallback")
        self.assertFalse(selection.response_preference)

    def test_generic_fallback_uses_write_mode_when_only_write_exists(self) -> None:
        response = self.resolver.resolve_response_mode(
            ["write"],
            "generic_fallback",
            True,
        )
        self.assertTrue(response)

    def test_no_writable_candidates_returns_none(self) -> None:
        services = [
            _FakeService(
                "0000ae30-0000-1000-8000-00805f9b34fb",
                [_FakeCharacteristic("0000ae02-0000-1000-8000-00805f9b34fb", ["notify"])],
            )
        ]
        self.assertIsNone(self.resolver.resolve(services))

    def test_regression_ff02_is_still_preferred(self) -> None:
        services = [
            _FakeService(
                "0000ff00-0000-1000-8000-00805f9b34fb",
                [
                    _FakeCharacteristic("12345678-0000-1000-8000-00805f9b34aa", ["write-without-response"]),
                    _FakeCharacteristic("0000ff02-0000-1000-8000-00805f9b34fb", ["write"]),
                ],
            )
        ]
        selection = self.resolver.resolve(services)
        self.assertIsNotNone(selection)
        self.assertEqual(selection.char_uuid, "0000ff02-0000-1000-8000-00805f9b34fb")
        self.assertEqual(selection.strategy, "preferred_uuid")


if __name__ == "__main__":
    unittest.main()
