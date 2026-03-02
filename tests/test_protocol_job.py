from __future__ import annotations

import importlib
import unittest

from tests.helpers import install_crc8_stub


class ProtocolJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.job = importlib.import_module("timiniprint.protocol.job")
        cls.types = importlib.import_module("timiniprint.protocol.types")

    def test_build_print_payload_contains_expected_sections(self) -> None:
        payload = self.job.build_print_payload(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            compress=False,
            lsb_first=True,
            new_format=False,
        )
        self.assertIn(bytes([0xAF]), payload)
        self.assertIn(bytes([0xBE]), payload)
        self.assertIn(bytes([0xBD]), payload)
        self.assertIn(bytes([0xA2]), payload)

    def test_build_job_appends_final_sequence(self) -> None:
        data = self.job.build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=10,
            energy=5000,
            blackening=3,
            compress=False,
            lsb_first=True,
            new_format=False,
            feed_padding=12,
            dev_dpi=203,
        )
        self.assertGreaterEqual(data.count(bytes([0xA1])), 2)
        self.assertIn(bytes([0xA3]), data)

    def test_build_from_raster_validates(self) -> None:
        raster = self.types.Raster(pixels=[1, 0, 1], width=2)
        with self.assertRaisesRegex(ValueError, "multiple of width"):
            self.job.build_job_from_raster(
                raster=raster,
                is_text=False,
                speed=10,
                energy=5000,
                blackening=3,
                compress=False,
                lsb_first=True,
                new_format=False,
                feed_padding=12,
                dev_dpi=203,
            )


if __name__ == "__main__":
    unittest.main()
