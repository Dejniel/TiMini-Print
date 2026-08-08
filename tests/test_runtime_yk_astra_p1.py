from __future__ import annotations

import asyncio
import unittest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.runtime.yk_astra_p1 import AstraP1RuntimeController
from timiniprint.protocol.families.yk_common import iter_yk_frames, pack_yk_frame
from timiniprint.protocol.families.yk_astra_p1.session import (
    paper_details_from_frame,
    status_from_frame,
)


def _status_packet(
    status_bits: int,
    *,
    command: int = 0x80,
    battery: int = 72,
) -> bytes:
    return pack_yk_frame(
        command,
        b"\x34\x12"
        + status_bits.to_bytes(2, "little")
        + bytes((9, 6, 25, battery)),
    )


class _AstraSession:
    def __init__(
        self,
        *,
        status_bits: int = 0,
        paper_details: bytes = b"\x01\x54\x5a\x02",
    ) -> None:
        self.status_bits = status_bits
        self.paper_details = paper_details
        self.queries: list[bytes] = []
        self.debug: list[str] = []
        self.warnings: list[tuple[str, str]] = []
        self.wait_reply = _status_packet(1 << 3, command=0x81) + _status_packet(
            0,
            command=0x81,
        )

    def can_query_control_packet(self) -> bool:
        return True

    def can_wait_for_reply(self) -> bool:
        return True

    async def query_control_packet(
        self,
        packet: bytes,
        *,
        timeout: float = 1.0,
        reply_complete=None,
    ) -> bytes:
        _ = timeout
        self.queries.append(bytes(packet))
        request = next(iter_yk_frames(packet))
        if request.command == 0x10:
            reply = _status_packet(self.status_bits)
        elif request.command == 0x72:
            self.assert_equal(request.payload, b"\x01")
            reply = pack_yk_frame(0x73, self.paper_details)
        else:
            raise AssertionError(f"unexpected Astra query 0x{request.command:02x}")
        if reply_complete is not None and not reply_complete(reply):
            raise AssertionError("Astra reply matcher rejected a valid response")
        return reply

    async def wait_for_reply(
        self,
        label: str,
        match,
        *,
        timeout: float,
        required: bool = True,
    ) -> bytes:
        _ = (label, timeout, required)
        if not match(self.wait_reply):
            raise AssertionError("Astra completion matcher rejected a valid transition")
        return self.wait_reply

    def report_debug(self, message: str) -> None:
        self.debug.append(message)

    def report_warning(self, *, short: str, detail: str) -> None:
        self.warnings.append((short, detail))

    @staticmethod
    def assert_equal(actual: bytes, expected: bytes) -> None:
        if actual != expected:
            raise AssertionError(f"expected {expected!r}, got {actual!r}")


class AstraP1RuntimeTests(unittest.TestCase):
    def test_s001_factory_enables_source_backed_paper_query(self) -> None:
        PrinterCatalog._cache.clear()
        device = PrinterCatalog.load().device_from_model("orgstra_s001")
        controller = runtime_controller_for_device(device)
        self.assertIsInstance(controller, AstraP1RuntimeController)
        assert isinstance(controller, AstraP1RuntimeController)
        session = _AstraSession(status_bits=(1 << 13) | (1 << 15))

        asyncio.run(controller.probe_capabilities(session, timeout=0.1))

        commands = [next(iter_yk_frames(packet)).command for packet in session.queries]
        self.assertEqual(commands, [0x10, 0x72])
        snapshot = controller.debug_snapshot()
        self.assertTrue(snapshot["valid_paper"])
        self.assertTrue(snapshot["paper_loaded"])
        self.assertEqual(
            snapshot["paper_details"],
            {"paper_type": 1, "width": 84, "length": 90, "color": 2},
        )

    def test_paper_query_is_skipped_without_valid_paper_status(self) -> None:
        controller = AstraP1RuntimeController(paper_query_on_valid=True)
        session = _AstraSession(status_bits=0)

        asyncio.run(controller.probe_capabilities(session, timeout=0.1))

        self.assertEqual(
            [next(iter_yk_frames(packet)).command for packet in session.queries],
            [0x10],
        )
        self.assertIsNone(controller.debug_snapshot()["paper_details"])

    def test_status_errors_are_reported_once_per_changed_bitmap(self) -> None:
        controller = AstraP1RuntimeController()
        session = _AstraSession()
        packet = _status_packet((1 << 8) | (1 << 11))

        controller.handle_notification(session, packet[:7])
        controller.handle_notification(session, packet[7:])
        controller.handle_notification(session, packet)

        self.assertEqual(len(session.warnings), 1)
        self.assertIn("cover open", session.warnings[0][1])
        self.assertIn("out of paper", session.warnings[0][1])

    def test_completion_wait_is_passive_and_requires_printing_to_idle(self) -> None:
        controller = AstraP1RuntimeController()
        session = _AstraSession()

        asyncio.run(controller.wait_for_completion(session, timeout=0.1))

        snapshot = controller.debug_snapshot()
        self.assertEqual(snapshot["completion_count"], 1)
        self.assertFalse(snapshot["saw_printing"])
        self.assertFalse(snapshot["finished"])
        self.assertEqual(session.queries, [])
        self.assertEqual(session.warnings, [])

    def test_reply_parsers_reject_lengths_not_accepted_by_native_parser(self) -> None:
        short_status = next(iter_yk_frames(pack_yk_frame(0x81, b"\x00" * 7)))
        long_paper = next(iter_yk_frames(pack_yk_frame(0x73, b"\x00" * 5)))

        self.assertIsNone(status_from_frame(short_status))
        self.assertIsNone(paper_details_from_frame(long_paper))

    def test_extended_status_uses_extended_word_for_paper_loaded(self) -> None:
        frame = next(
            iter_yk_frames(
                pack_yk_frame(
                    0xFF,
                    b"\x04\x00\x00\x00\x09\x06\x19" + b"\x00" * 5,
                )
            )
        )
        status = status_from_frame(frame)

        self.assertIsNotNone(status)
        assert status is not None
        self.assertTrue(status.paper_loaded)


if __name__ == "__main__":
    unittest.main()
