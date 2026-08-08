from __future__ import annotations

import unittest

from timiniprint.protocol.families.yk_common import (
    YkFrameStreamDecoder,
    iter_yk_frames,
    pack_yk_frame,
    resequence_yk_frame,
)


class YkFrameCodecTests(unittest.TestCase):
    def test_zero_integrity_frame_matches_wire_format(self) -> None:
        self.assertEqual(
            pack_yk_frame(0x0A, b"\x19", sequence=0),
            bytes.fromhex("64 0a 00 01 00 19 00 00 00 00 9b"),
        )

    def test_decoder_accepts_zero_and_calculated_integrity(self) -> None:
        zero = pack_yk_frame(0x10, sequence=3)
        calculated = pack_yk_frame(
            0x73,
            b"\x01\x0c\x20\x00",
            sequence=4,
            calculated_integrity=True,
        )
        decoder = YkFrameStreamDecoder()

        self.assertEqual(decoder.feed(b"noise" + zero[:4]), ())
        frames = decoder.feed(zero[4:] + calculated)

        self.assertEqual([frame.command for frame in frames], [0x10, 0x73])
        self.assertEqual(frames[1].payload, b"\x01\x0c\x20\x00")
        self.assertNotEqual(frames[1].integrity, 0)
        self.assertEqual(decoder.pending, b"")

    def test_sequence_wrap_and_resequence_use_modulo_64(self) -> None:
        wrapped = pack_yk_frame(0x11, sequence=65)
        self.assertEqual(next(iter_yk_frames(wrapped)).sequence, 1)

        resequenced = resequence_yk_frame(wrapped, sequence=127)

        self.assertEqual(next(iter_yk_frames(resequenced)).sequence, 63)


if __name__ == "__main__":
    unittest.main()
