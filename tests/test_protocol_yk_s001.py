from __future__ import annotations

import unittest

from timiniprint.protocol.families import get_protocol_behavior
from timiniprint.protocol.families.base import PrintJobRequest
from timiniprint.protocol.families.yk_s001 import core
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding, ImagePipelineConfig, PaperMode
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


def _request(pixels, width, *, paper_mode=PaperMode.TAG, speed=9, one_length=0):
    raster = RasterBuffer(pixels=pixels, width=width, pixel_format=PixelFormat.BW1)
    return PrintJobRequest(
        raster_set=RasterSet.from_single(raster),
        image_pipeline=ImagePipelineConfig(
            formats=(PixelFormat.BW1,),
            encoding=ImageEncoding.YK_S001_RASTER,
        ),
        is_text=False,
        speed=speed,
        energy=0,
        blackening=0,
        lsb_first=False,
        protocol_family=ProtocolFamily.YK_S001,
        protocol_variant=None,
        feed_padding=0,
        dev_dpi=203,
        paper_mode=paper_mode,
        one_length=one_length,
    )


def _parse_frames(buf):
    """Yield (cmd, seq, payload) tuples; validates the envelope."""
    i = 0
    frames = []
    while i < len(buf):
        assert buf[i] == core.FRAME_START, "bad start"
        cmd, seq = buf[i + 1], buf[i + 2]
        ln = buf[i + 3] | (buf[i + 4] << 8)
        payload = buf[i + 5:i + 5 + ln]
        chk = buf[i + 5 + ln:i + 9 + ln]
        end = buf[i + 9 + ln]
        assert chk == core.FRAME_CHECKSUM and end == core.FRAME_END, "bad envelope"
        frames.append((cmd, seq, payload))
        i += 10 + ln
    return frames


class YkS001FrameTests(unittest.TestCase):
    def test_frame_envelope(self) -> None:
        seq = core._Seq()
        self.assertEqual(
            core._frame(core.CMD_SET_WIDTH, b"\x19", seq),
            bytes.fromhex("640a00010019000000009b"),
        )

    def test_sequence_counter_increments_and_wraps(self) -> None:
        seq = core._Seq(start=0xFF)
        self.assertEqual(seq.take(), 0xFF)
        self.assertEqual(seq.take(), 0x00)
        self.assertEqual(seq.take(), 0x01)

    def test_named_frame_builders(self) -> None:
        self.assertEqual(core.set_width_frame(25, core._Seq()), bytes.fromhex("640a00010019000000009b"))
        self.assertEqual(core.set_speed_frame(9, core._Seq()), bytes.fromhex("6409000100 09000000009b".replace(" ", "")))
        self.assertEqual(core.set_paper_type_frame(core.PAPER_TYPE_GAP, core._Seq()),
                         bytes.fromhex("64280002000101000000009b"))
        self.assertEqual(core.label_config_frame(core._Seq()),
                         bytes.fromhex("6403000300022003000000009b"))
        self.assertEqual(core.print_trigger_frame(core._Seq()),
                         bytes.fromhex("6403000300012003000000009b"))
        self.assertEqual(core.set_feed_frame(21, core._Seq()),
                         bytes.fromhex("640200020015000000 00009b".replace(" ", "")))


class YkS001RasterTests(unittest.TestCase):
    # Orientation: pixels are row-major, ``head_width`` per row (across the head,
    # <= usable head), ``column_count`` rows (along the feed). Each printed column
    # is ``BYTES_PER_COL`` (12) bytes; the along-feed axis is x-mirrored.
    def test_single_black_pixel_uses_top_pad_and_mirroring(self) -> None:
        # head_width 1, column_count 1: head 0 -> bit 6 (top pad) -> 0x02
        self.assertEqual(core.pack_raster([1], 1, 1), b"\x02" + b"\x00" * (core.BYTES_PER_COL - 1))

    def test_along_feed_axis_is_mirrored(self) -> None:
        # column_count 2: the FIRST image row lands in the LAST printed column
        data = core.pack_raster([1, 0], 1, 2)
        self.assertEqual(data[core.BYTES_PER_COL], 0x02)   # last column <- row 0
        self.assertEqual(data[0], 0x00)                    # first column <- row 1

    def test_head_bit_packing_is_msb_first_after_top_pad(self) -> None:
        # head 0 -> bit 6 -> 0x02, head 1 -> bit 7 -> 0x01, head 2 -> byte1 0x80
        self.assertEqual(core.pack_raster([1, 0, 0], 3, 1)[0], 0x02)
        self.assertEqual(core.pack_raster([0, 1, 0], 3, 1)[0], 0x01)
        self.assertEqual(core.pack_raster([0, 0, 1], 3, 1)[1], 0x80)

    def test_buffer_is_bytes_per_col_per_column(self) -> None:
        data = core.pack_raster([0] * (10 * 4), 10, 4)
        self.assertEqual(len(data), 4 * core.BYTES_PER_COL)

    def test_head_width_clipped_to_usable_head(self) -> None:
        # head dots beyond the usable head (HEAD_DOTS - TOP_PAD_BITS) are ignored
        data = core.pack_raster([1] * (core.HEAD_DOTS + 16), core.HEAD_DOTS + 16, 1)
        self.assertEqual(len(data), core.BYTES_PER_COL)


class YkS001FeedTests(unittest.TestCase):
    def test_trailing_feed_formula(self) -> None:
        self.assertEqual(core._trailing_feed_dots(320, 280, 2.5), 21)

    def test_feed_frame_omitted_when_not_positive(self) -> None:
        job = get_protocol_behavior(ProtocolFamily.YK_S001).job_builder(
            _request([1], 1, one_length=0)  # label length == width -> negative feed
        )
        cmds = [cmd for cmd, _seq, _p in _parse_frames(job.payload)]
        self.assertNotIn(core.CMD_SET_FEED, cmds)

    def test_feed_frame_present_when_label_longer_than_image(self) -> None:
        # portrait image: head_width 1, column_count 280; label length 320 -> feed 21
        job = get_protocol_behavior(ProtocolFamily.YK_S001).job_builder(
            _request([0] * 280, 1, one_length=320)
        )
        feed = [p for cmd, _s, p in _parse_frames(job.payload) if cmd == core.CMD_SET_FEED]
        self.assertEqual(feed, [bytes.fromhex("1500")])  # 21 dots, LE


class YkS001JobTests(unittest.TestCase):
    def test_paper_mode_selects_paper_type_byte(self) -> None:
        expected = {
            PaperMode.TAG: core.PAPER_TYPE_GAP,
            PaperMode.BLACK_TAG: core.PAPER_TYPE_MARK,
            PaperMode.PLAIN: core.PAPER_TYPE_CONTINUOUS,
        }
        for paper_mode, code in expected.items():
            with self.subTest(paper_mode=paper_mode):
                job = get_protocol_behavior(ProtocolFamily.YK_S001).job_builder(
                    _request([0], 1, paper_mode=paper_mode)
                )
                pt = [p for cmd, _s, p in _parse_frames(job.payload)
                      if cmd == core.CMD_SET_PAPER_TYPE]
                self.assertEqual(pt[0], bytes((0x01, code)))

    def test_job_frame_order_and_sequence(self) -> None:
        job = get_protocol_behavior(ProtocolFamily.YK_S001).job_builder(
            _request([0] * (280 * 4), 280, one_length=320)
        )
        frames = _parse_frames(job.payload)
        cmds = [cmd for cmd, _s, _p in frames]
        # setup, feed, raster..., trigger
        self.assertEqual(cmds[:5], [
            core.CMD_SET_WIDTH, core.CMD_SET_SPEED, core.CMD_SET_PAPER_TYPE,
            core.CMD_LABEL, core.CMD_SET_FEED,
        ])
        self.assertEqual(cmds[-1], core.CMD_LABEL)
        self.assertTrue(all(c == core.CMD_RASTER for c in cmds[5:-1]))
        # sequence numbers are strictly consecutive from 0
        self.assertEqual([s for _c, s, _p in frames], list(range(len(frames))))

    def test_golden_single_pixel_job(self) -> None:
        job = get_protocol_behavior(ProtocolFamily.YK_S001).job_builder(
            _request([1], 1, paper_mode=PaperMode.TAG, speed=9, one_length=0)
        )
        expected = b"".join((
            bytes.fromhex("640a00010019000000009b"),    # seq0 width 25
            bytes.fromhex("640901010009000000009b"),    # seq1 speed 9
            bytes.fromhex("64280202000101000000009b"),  # seq2 paper type gap
            bytes.fromhex("6403030300022003000000009b"),  # seq3 label config
            bytes.fromhex("6400040c00") + b"\x02" + b"\x00" * 11 + core.FRAME_CHECKSUM + b"\x9b",  # seq4 raster
            bytes.fromhex("6403050300012003000000009b"),  # seq5 print trigger
        ))
        self.assertEqual(job.payload, expected)


if __name__ == "__main__":
    unittest.main()
