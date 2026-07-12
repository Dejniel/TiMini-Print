from __future__ import annotations

from collections import defaultdict, deque


_END_MARKER = bytes((0x11, 0x00, 0x00))
_MAX_DISTANCE = 16_384
_MIN_MATCH = 3


def compress_lzo1x_1(data: bytes) -> bytes:
    """Encode one raw LZO1X block.

    The encoder emits the standard version-0 instruction stream understood by
    LZO1X decompressors. It uses literal runs and the 16 KiB match form; the
    other match forms only make the stream smaller and are not required for
    compatibility.
    """

    source = bytes(data)
    if not source:
        return _END_MARKER

    matches = _find_matches(source)
    output = bytearray()
    if not matches:
        _append_literals(output, source, initial=True)
    else:
        _append_literals(output, source[: matches[0][0]], initial=True)
        for index, (start, length, distance) in enumerate(matches):
            end = start + length
            next_start = matches[index + 1][0] if index + 1 < len(matches) else len(source)
            gap = next_start - end
            trailing = source[end:next_start] if gap <= 3 else b""
            _append_match(output, length, distance, trailing)
            if gap >= 4:
                _append_literals(output, source[end:next_start])

    output.extend(_END_MARKER)
    return bytes(output)


def _find_matches(data: bytes) -> list[tuple[int, int, int]]:
    positions: defaultdict[bytes, deque[int]] = defaultdict(deque)
    matches: list[tuple[int, int, int]] = []
    position = 0

    while position + _MIN_MATCH <= len(data):
        key = data[position : position + _MIN_MATCH]
        candidates = positions[key]
        while candidates and position - candidates[0] > _MAX_DISTANCE:
            candidates.popleft()

        best_length = 0
        best_distance = 0
        for candidate in reversed(candidates):
            length = _MIN_MATCH
            while (
                position + length < len(data)
                and data[candidate + length] == data[position + length]
            ):
                length += 1
            if length > best_length:
                best_length = length
                best_distance = position - candidate
            if position + length == len(data):
                break

        if best_length >= _MIN_MATCH:
            matches.append((position, best_length, best_distance))
            match_end = position + best_length
            for matched_position in range(position, match_end):
                if matched_position + _MIN_MATCH <= len(data):
                    match_key = data[matched_position : matched_position + _MIN_MATCH]
                    positions[match_key].append(matched_position)
            position = match_end
        else:
            candidates.append(position)
            position += 1

    return matches


def _append_literals(output: bytearray, literals: bytes, *, initial: bool = False) -> None:
    length = len(literals)
    if length == 0:
        return

    if initial and length <= 238:
        output.append(17 + length)
    else:
        if length < 4:
            raise ValueError("non-initial LZO literal runs require at least four bytes")
        if length <= 18:
            output.append(length - 3)
        else:
            output.append(0)
            _append_length_extension(output, length - 18)
    output.extend(literals)


def _append_match(
    output: bytearray,
    length: int,
    distance: int,
    trailing_literals: bytes,
) -> None:
    if length < _MIN_MATCH:
        raise ValueError("LZO matches require at least three bytes")
    if not 1 <= distance <= _MAX_DISTANCE:
        raise ValueError("LZO match distance exceeds the 16 KiB window")
    if len(trailing_literals) > 3:
        raise ValueError("LZO match instructions can carry at most three literals")

    encoded_length = length - 2
    if encoded_length <= 31:
        output.append(0x20 | encoded_length)
    else:
        output.append(0x20)
        _append_length_extension(output, encoded_length - 31)

    operand = ((distance - 1) << 2) | len(trailing_literals)
    output.extend((operand & 0xFF, operand >> 8))
    output.extend(trailing_literals)


def _append_length_extension(output: bytearray, value: int) -> None:
    if value <= 0:
        raise ValueError("LZO length extensions must be positive")
    while value > 255:
        output.append(0)
        value -= 255
    output.append(value)
