from __future__ import annotations

import unittest

from timiniprint.devices.models import PrinterModelMacAlias


class ModelAliasMacGuardTests(unittest.TestCase):
    def test_mac_alias_does_not_match_uuid_address(self) -> None:
        alias = PrinterModelMacAlias(suffixes=["59"], map_model_head_name="GT02-")
        self.assertFalse(alias.matches("F4B3C8E3-C284-9C3A-C549-D786345CB553"))

    def test_mac_alias_matches_mac_address_suffix(self) -> None:
        alias = PrinterModelMacAlias(suffixes=["59"], map_model_head_name="GT02-")
        self.assertTrue(alias.matches("AA:BB:CC:DD:EE:59"))
        self.assertTrue(alias.matches("AA-BB-CC-DD-EE-59"))


if __name__ == "__main__":
    unittest.main()
