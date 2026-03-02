from __future__ import annotations

import importlib
import unittest

from tests.helpers import install_crc8_stub, load_golden_hex


class ProtocolGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_crc8_stub()
        cls.job = importlib.import_module("timiniprint.protocol.job")
        cls.golden = load_golden_hex("tests/fixtures/protocol_golden.json")

    def test_golden_old_format(self) -> None:
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
        self.assertEqual(data.hex(), self.golden["image_old"])

    def test_golden_new_format(self) -> None:
        data = self.job.build_job(
            pixels=[1, 1, 0, 0, 1, 1, 0, 0],
            width=8,
            is_text=True,
            speed=8,
            energy=8000,
            blackening=4,
            compress=False,
            lsb_first=False,
            new_format=True,
            feed_padding=7,
            dev_dpi=300,
        )
        self.assertEqual(data.hex(), self.golden["text_new"])

    def test_golden_compress_fallback_raw(self) -> None:
        data = self.job.build_job(
            pixels=[1, 0, 1, 0, 1, 0, 1, 0],
            width=8,
            is_text=False,
            speed=9,
            energy=6000,
            blackening=2,
            compress=True,
            lsb_first=False,
            new_format=False,
            feed_padding=6,
            dev_dpi=203,
        )
        self.assertEqual(data.hex(), self.golden["compress_fallback_raw"])


if __name__ == "__main__":
    unittest.main()
