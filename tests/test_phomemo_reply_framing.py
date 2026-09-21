import pytest

from timiniprint.protocol.families.phomemo_esc.replies import (
    PhomemoReplyDecoder, PrintMasterReplyDecoder, phomemo_reply,
)


@pytest.mark.parametrize("decoder_type,prefix,accepted", [
    (PhomemoReplyDecoder, b"", False),
    (PhomemoReplyDecoder, b"\x1a", True),
    (PhomemoReplyDecoder, b"\x1b", False),
    (PrintMasterReplyDecoder, b"", True),
    (PrintMasterReplyDecoder, b"\x1a", True),
    (PrintMasterReplyDecoder, b"\x1b", True),
])
def test_shared_framing_preserves_dialect_prefix_rules(decoder_type, prefix, accepted):
    stream = prefix + bytes.fromhex("0599") + prefix + bytes.fromhex("0f0c")
    expected = [bytes.fromhex("0599"), bytes.fromhex("0f0c")] if accepted else []
    for split in range(len(stream) + 1):
        decoder = decoder_type()
        assert decoder.feed(stream[:split]) + decoder.feed(stream[split:]) == expected
        assert not decoder.has_pending_frame


@pytest.mark.parametrize("decoder_type,features", [
    (PhomemoReplyDecoder, bytes.fromhex("3b00041a0f0c")),
    (PrintMasterReplyDecoder, bytes.fromhex("3b001a0f")),
])
def test_capability_lengths_and_embedded_statuses_remain_dialect_specific(decoder_type, features):
    serial = b"\x08Q194" + bytes.fromhex("1a0f0c1b05991a06880000")
    frames = [features, serial, bytes.fromhex("0f0c")]
    stream = b"\xff\xfe" + b"".join(b"\x1a" + frame for frame in frames)
    for split in range(len(stream) + 1):
        decoder = decoder_type()
        assert decoder.feed(stream[:split]) + decoder.feed(stream[split:]) == frames
        assert not decoder.has_pending_frame
    decoder = decoder_type()
    assert [frame for byte in stream for frame in decoder.feed(bytes((byte,)))] == frames
    assert not decoder.has_pending_frame


@pytest.mark.parametrize("decoder_type,frame", [
    (PhomemoReplyDecoder, bytes.fromhex("1a3b00041a0f0c")),
    (PrintMasterReplyDecoder, bytes.fromhex("1b3b001a0f")),
])
def test_incomplete_frames_survive_empty_reads(decoder_type, frame):
    decoder = decoder_type()
    assert decoder.feed(frame[:-1]) == []
    assert decoder.has_pending_frame
    assert decoder.feed(b"") == []
    assert decoder.has_pending_frame
    assert decoder.feed(frame[-1:]) == [frame[1:]]
    assert not decoder.has_pending_frame


def test_phomemo_query_selects_latest_matching_frame_not_payload_bytes():
    data = bytes.fromhex("1a1d01 1a3b001a1d0200 1a1d00 1a08")
    assert phomemo_reply(data, (0x1D, 0x17)) == b"\x00"
    assert phomemo_reply(data, None) == b"\x00"
    assert phomemo_reply(data, 0x08) is None
