from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import install_crc8_stub

install_crc8_stub()

from timiniprint.protocol import ImageEncoding, ProtocolJob  # noqa: E402
from timiniprint.protocol.family import ProtocolFamily  # noqa: E402
from timiniprint.protocol.packet import make_packet  # noqa: E402
from tools import debug_protocol_job  # noqa: E402


class DebugProtocolToolTests(unittest.TestCase):
    def test_dump_runtime_preset_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dump_path = f"{tmpdir}/job.json"
            packet = make_packet(0xA4, b"\x35", ProtocolFamily.V5G)

            with patch(
                "tools.debug_protocol_job.cli.build_print_job",
                return_value=ProtocolJob(payload=packet),
            ) as build_job:
                code = debug_protocol_job.main(
                    [
                        "--runtime-preset",
                        "mx06",
                        "--out",
                        dump_path,
                        "--text",
                        "hello",
                        "--image-encoding",
                        "v5g_gray",
                        "--darkness",
                        "5",
                    ]
                )

            self.assertEqual(code, 0)
            build_job.assert_called_once()
            self.assertEqual(
                build_job.call_args.kwargs["image_encoding_override"],
                ImageEncoding.V5G_GRAY,
            )
            with open(dump_path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["schema"], "timiniprint/debug-protocol-job/v1")
            self.assertTrue(payload["diagnostic_only"])
            self.assertEqual(payload["device"]["profile_key"], "v5g_small_203")
            self.assertEqual(payload["device"]["profile_runtime_preset_key"], "mx06")
            self.assertEqual(payload["device"]["protocol_family"], "v5g")
            self.assertEqual(payload["settings"]["darkness"], 5)
            self.assertEqual(payload["settings"]["image_encoding_override"], "v5g_gray")
            self.assertEqual(payload["job"]["effective_image_pipeline"]["encoding"], "v5g_gray")
            self.assertEqual(payload["job"]["effective_image_pipeline"]["formats"][0], "gray4")
            self.assertIn("connect_packets", payload["transport"])
            self.assertEqual(payload["job"]["payload_bytes"], len(packet))
            self.assertEqual(payload["packets"][0]["op"], "A4")
            self.assertEqual(payload["payload_hex"], packet.hex())


if __name__ == "__main__":
    unittest.main()
