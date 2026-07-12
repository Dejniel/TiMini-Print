from __future__ import annotations

import random
import unittest

from timiniprint.protocol.compression import compress_lzo1x_1


class Lzo1xCompressionTests(unittest.TestCase):
    def test_known_literal_blocks(self) -> None:
        self.assertEqual(compress_lzo1x_1(b""), bytes.fromhex("110000"))
        self.assertEqual(compress_lzo1x_1(b"A"), bytes.fromhex("1241110000"))
        self.assertEqual(
            compress_lzo1x_1(b"ABCD"),
            bytes.fromhex("1541424344110000"),
        )

    def test_long_literal_block_uses_extended_length(self) -> None:
        source = bytes(range(239))
        compressed = compress_lzo1x_1(source)

        self.assertEqual(compressed[:2], bytes((0x00, 0xDD)))
        self.assertEqual(compressed[2:-3], source)
        self.assertEqual(compressed[-3:], bytes.fromhex("110000"))

    def test_repeated_raster_data_is_compressed(self) -> None:
        source = bytes((position // 48) % 16 * 17 for position in range(7_680))
        compressed = compress_lzo1x_1(source)

        self.assertLess(len(compressed), len(source) // 10)
        self.assertEqual(_decode_emitted_stream(compressed), source)

    def test_round_trip_varied_blocks(self) -> None:
        rng = random.Random(0x1050)
        blocks = [
            bytes(length)
            for length in (0, 1, 2, 3, 4, 18, 19, 238, 239, 255, 256, 1_024)
        ]
        blocks.extend(
            bytes(rng.randrange(256) for _ in range(length))
            for length in (3, 17, 257, 3_840, 7_680)
        )
        blocks.extend(
            (b"ABCD" * ((length + 3) // 4))[:length]
            for length in (31, 256, 4_096, 16_640)
        )

        for source in blocks:
            with self.subTest(length=len(source), prefix=source[:8]):
                self.assertEqual(
                    _decode_emitted_stream(compress_lzo1x_1(source)),
                    source,
                )


def _decode_emitted_stream(stream: bytes) -> bytes:
    """Decode the instruction subset emitted by TiMini's LZO1X encoder."""

    position = 0
    output = bytearray()

    token = stream[position]
    if token >= 18:
        position += 1
        literal_length = token - 17
        output.extend(stream[position : position + literal_length])
        position += literal_length
    elif token <= 15:
        position += 1
        literal_length, position = _decode_length(stream, position, token, 15, 3)
        output.extend(stream[position : position + literal_length])
        position += literal_length

    while stream[position : position + 3] != bytes.fromhex("110000"):
        token = stream[position]
        position += 1
        if token <= 15:
            literal_length, position = _decode_length(stream, position, token, 15, 3)
            output.extend(stream[position : position + literal_length])
            position += literal_length
            continue
        if not 32 <= token <= 63:
            raise AssertionError(f"unexpected LZO instruction: {token:#x}")

        match_length, position = _decode_length(stream, position, token & 0x1F, 31, 2)
        operand = int.from_bytes(stream[position : position + 2], "little")
        position += 2
        trailing_length = operand & 0x03
        distance = (operand >> 2) + 1
        for _ in range(match_length):
            output.append(output[-distance])
        output.extend(stream[position : position + trailing_length])
        position += trailing_length

    if position + 3 != len(stream):
        raise AssertionError("data follows the LZO end marker")
    return bytes(output)


def _decode_length(
    stream: bytes,
    position: int,
    encoded: int,
    base: int,
    constant: int,
) -> tuple[int, int]:
    if encoded:
        return encoded + constant, position

    length = base
    while stream[position] == 0:
        length += 255
        position += 1
    length += stream[position]
    return length + constant, position + 1


if __name__ == "__main__":
    unittest.main()
