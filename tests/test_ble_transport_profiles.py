from __future__ import annotations

import unittest

from timiniprint.protocol.families import get_protocol_behavior
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.transport.bluetooth.profiles import get_ble_transport_profile


class BleTransportProfileTests(unittest.TestCase):
    def test_protocol_behavior_does_not_own_transport_configuration(self) -> None:
        for family in ProtocolFamily:
            with self.subTest(family=family.value):
                self.assertFalse(hasattr(get_protocol_behavior(family), "transport"))

    def test_family_transport_profiles_keep_endpoint_and_pacing_values(self) -> None:
        expected = {
            ProtocolFamily.TINY: (512, 50, "", ""),
            ProtocolFamily.TINY_PREFIXED: (512, 50, "", ""),
            ProtocolFamily.V5G: (56 * 8, 30, "", ""),
            ProtocolFamily.V5C: (20, 50, "", ""),
            ProtocolFamily.V5X: (
                20,
                50,
                "0000ae30-0000-1000-8000-00805f9b34fb",
                "",
            ),
            ProtocolFamily.PHOMEMO_ESC: (
                128,
                20,
                "0000ff00-0000-1000-8000-00805f9b34fb",
                "0000ff02-0000-1000-8000-00805f9b34fb",
            ),
            ProtocolFamily.NIIMBOT: (
                20,
                10,
                "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
                "",
            ),
            ProtocolFamily.ELEPH_HPRT_ESC: (180, 10, "", ""),
            ProtocolFamily.ELEPH_TSPL: (
                180,
                10,
                "000018f0-0000-1000-8000-00805f9b34fb",
                "00002af1-0000-1000-8000-00805f9b34fb",
            ),
            ProtocolFamily.INSTAPRINT_CORE: (180, 10, "", ""),
            ProtocolFamily.FUNNY_LX: (
                100,
                0,
                "0000ffe6-0000-1000-8000-00805f9b34fb",
                "0000ffe1-0000-1000-8000-00805f9b34fb",
            ),
        }

        for family, values in expected.items():
            with self.subTest(family=family.value):
                profile = get_ble_transport_profile(family)
                self.assertEqual(
                    (
                        profile.standard_chunk_cap,
                        profile.standard_write_delay_ms,
                        profile.preferred_service_uuid,
                        profile.preferred_write_char_uuid,
                    ),
                    values,
                )

    def test_v5x_bulk_and_flow_settings_are_transport_mechanics_only(self) -> None:
        v5x = get_ble_transport_profile(ProtocolFamily.V5X)
        self.assertIsNotNone(v5x.bulk_write)
        assert v5x.bulk_write is not None
        self.assertEqual(v5x.bulk_write.char_uuid, "0000ae03-0000-1000-8000-00805f9b34fb")
        self.assertEqual(v5x.bulk_write.chunk_cap, 180)
        self.assertEqual(v5x.bulk_write.write_delay_ms, 30)
        self.assertTrue(v5x.bulk_write.flow_controlled)
        self.assertEqual(v5x.write_without_response_payload_reserve, 5)

        v5c = get_ble_transport_profile(ProtocolFamily.V5C)
        self.assertTrue(v5c.flow_controlled_standard_write)


if __name__ == "__main__":
    unittest.main()
