import asyncio
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.errors import PrinterNotReadyError
from timiniprint.printing.builder import PrintJobBuilder
from timiniprint.printing.document_renderer import DocumentPage, DocumentPlan, DocumentRenderer, RenderDocument, RenderedPage
from timiniprint.printing.settings import PrintSettings
from timiniprint.printing.runtime.base import PreparedPrinter
from timiniprint.printing.runtime.session import RuntimeConnectionSession
from timiniprint import reporting
from timiniprint.printing.runtime.factory import runtime_controller_for_device
from timiniprint.printing.runtime.xeasylabel import XEasyLabelRuntimeController
from timiniprint.printing.send import send_prepared_job
from timiniprint.protocol import ImageEncoding, ImagePipelineConfig, PaperMode, ProtocolFamily, ProtocolJob, ProtocolStep
from timiniprint.protocol.families.base import PrintJobRequest
from timiniprint.protocol.families.yk_astra_p1.core import PUBLIC_RECIPE
from timiniprint.protocol.families.yk_astra_p1.session import status_from_frame
from timiniprint.protocol.families.yk_common import iter_yk_frames, pack_yk_frame
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet
from timiniprint.rendering.converters.base import rotate_image
from timiniprint.rendering.converters import Page


def request(*, mode=PaperMode.PLAIN, height=160, top=0, first=True, last=True):
    return PrintJobRequest(
        raster_set=RasterSet.from_single(RasterBuffer([0] * 96 * (height + top), 96, PixelFormat.BW1)),
        image_pipeline=ImagePipelineConfig((PixelFormat.BW1,), ImageEncoding.YK_ASTRA_P1_RAW),
        is_text=False, speed=25, density=9, energy=0, blackening=3, lsb_first=False,
        protocol_family=ProtocolFamily.YK_ASTRA_P1, protocol_variant="xeasylabel_hp6_c",
        feed_padding=0, dev_dpi=203, paper_mode=mode, top_padding_pixels=top,
        page_index=1 if first else 2, page_count=(1 if first else 2) if last else 3,
    )


def status(bits=0x1003, *, command=0xFF, extended=None):
    data = b"\x34\x12" + bits.to_bytes(2, "little") + bytes((9, 3, 25, 75))
    if extended is not None:
        data = extended.to_bytes(2, "little") + data[2:] + b"\x00" * 4
    return pack_yk_frame(command, data, calculated_integrity=True)


def test_hp6_c_matches_captured_single_continuous_commands_and_chunks():
    frames = tuple(iter_yk_frames(PUBLIC_RECIPE.build_job(request()).payload))
    assert [(f.command, f.payload.hex()) for f in frames if f.command] == [
        (0x0A, "19"), (0x09, "09"), (0x03, "122000"), (0x03, "112000"), (0x02, "0800"),
    ]
    assert [len(f.payload) for f in frames if not f.command] == [288] * 6 + [192]
    assert [f.sequence for f in frames] == list(range(1, len(frames) + 1))
    assert all(f.integrity == 0 for f in frames)


@pytest.mark.parametrize("width,padding,first_row,second_row", [
    (9, 0, b"\x80\x80" + bytes(10), b"\x40" + bytes(11)),
    (9, 7, b"\x01\x01" + bytes(10), b"\x00\x80" + bytes(10)),
    (9, 92, bytes(11) + b"\x08", bytes(11) + b"\x04"),
    (9, 95, bytes(11) + b"\x01", bytes(12)),
    (9, 96, bytes(12), bytes(12)),
    (101, 0, b"\x80" + bytes(11), b"\x40" + bytes(11)),
    (9, -5, b"\x80\x80" + bytes(10), b"\x40" + bytes(11)),
])
def test_raster_padding_and_head_clipping_keep_msb_pixel_positions(width, padding, first_row, second_row):
    pixels = [0] * (width * 2)
    pixels[0] = pixels[width - 1] = pixels[width + 1] = 1
    job_request = replace(request(height=2), left_padding_pixels=padding,
        raster_set=RasterSet.from_single(RasterBuffer(pixels, width, PixelFormat.BW1)))
    frames = iter_yk_frames(PUBLIC_RECIPE.build_job(job_request).payload)
    assert b"".join(frame.payload for frame in frames if frame.command == 0) == first_row + second_row


@pytest.mark.parametrize("first,last", [(True, False), (False, False), (False, True)])
def test_hp6_c_copy_phases(first, last):
    frames = tuple(iter_yk_frames(PUBLIC_RECIPE.build_job(request(first=first, last=last)).payload))
    controls = [(f.command, f.payload.hex()) for f in frames if f.command]
    assert controls == [(0x0A, "19"), (0x09, "09")] + ([(3, "122000")] if first else []) + (
        [(3, "112000"), (2, "0800")] if last else [(2, "5000")]
    )


def test_gap_corrects_start_without_scaling_and_keeps_one_row():
    frames = tuple(iter_yk_frames(PUBLIC_RECIPE.build_job(request(mode=PaperMode.TAG)).payload))
    assert sum(len(f.payload) for f in frames if not f.command) == 154 * 12
    assert [(f.command, f.payload.hex()) for f in frames if f.command] == [
        (10, "19"), (9, "09"), (3, "022003"), (3, "012003"),
    ]
    tiny = tuple(iter_yk_frames(PUBLIC_RECIPE.build_job(request(mode=PaperMode.TAG, height=1)).payload))
    assert sum(len(f.payload) for f in tiny if not f.command) == 12
    positioned = tuple(iter_yk_frames(PUBLIC_RECIPE.build_job(request(mode=PaperMode.TAG, height=280, top=20)).payload))
    assert (2, "0b00") in [(f.command, f.payload.hex()) for f in positioned]
    assert sum(len(f.payload) for f in positioned if not f.command) == 280 * 12


def test_hp6_c_catalog_orientation_and_default_band():
    device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
    assert device.profile.use_spp
    assert isinstance(runtime_controller_for_device(device), XEasyLabelRuntimeController)
    paper = device.profile.default_paper_preset
    assert (paper.rotation_degrees, paper.render_width_px, paper.left_padding_px) == (270, 88, 5)
    assert not paper.mirror_horizontal
    image = Image.new("L", (2, 3))
    image.putdata([10, 20, 30, 40, 50, 60])
    assert list(rotate_image(image, paper.rotation_degrees).getdata()) == [20, 40, 60, 10, 30, 50]


@pytest.mark.parametrize("gap", [False, True])
def test_file_pipeline_rotates_once_and_preserves_physical_band_and_feed(gap):
    device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
    paper = device.profile.paper_preset("xeasy_96_bm1_tag_12x40") if gap else device.profile.default_paper_preset
    image = Image.new("L", (280, 80) if gap else (160, 88), 255)
    image.putpixel((image.width - 1, 0), 0)
    image.putpixel((0, image.height - 1), 0)
    settings = PrintSettings(paper_preset_key=paper.key, feed_padding=0,
        trim_side_margins=False, trim_top_bottom_margins=False)
    builder = PrintJobBuilder(device, settings, document_renderer=DocumentRenderer(image_loader=lambda _: image.copy()))
    with patch("timiniprint.printing.builder.os.path.isfile", return_value=True):
        job = builder.build_from_file("test.png")
    frames = tuple(iter_yk_frames(job.payload))
    raster = b"".join(f.payload for f in frames if not f.command)
    assert len(raster) == image.width * 12
    left = paper.left_padding_px
    assert raster[left // 8] == 0x80 >> (left % 8)
    right = left + image.height - 1
    assert raster[-12 + right // 8] == 0x80 >> (right % 8)
    if gap:
        assert (2, "0b00") in [(f.command, f.payload.hex()) for f in frames]


def test_long_text_is_one_raster_job_without_intermediate_feed(tmp_path):
    device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
    settings = PrintSettings(feed_padding=0)
    path = tmp_path / "long.txt"
    path.write_text("\n".join(f"line {i}" for i in range(30)), encoding="utf-8")
    jobs = list(PrintJobBuilder(device, settings).iter_page_jobs(str(path)))
    assert len(jobs) == 1
    assert jobs[0].page_count > 1
    controls = [(frame.command, frame.payload.hex()) for frame in iter_yk_frames(jobs[0].job.payload) if frame.command]
    assert controls == [(10, "19"), (9, "09"), (3, "122000"), (3, "112000"), (2, "0800")]


def test_independent_pages_are_not_merged_as_text_fragments():
    device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
    renderer = DocumentRenderer()
    plan = DocumentPlan(RenderDocument("two.pdf"), "pdf", (DocumentPage(0), DocumentPage(1)))
    rendered = RenderedPage(
        source_page=Page(Image.new("1", (88, 16), 1), dither=False, is_text=False),
        raster_set=RasterSet.from_single(RasterBuffer([0] * 88 * 16, 88, PixelFormat.BW1)),
        image_pipeline=ImagePipelineConfig((PixelFormat.BW1,), ImageEncoding.YK_ASTRA_P1_RAW),
    )
    with patch.object(renderer, "plan_document", return_value=plan):
        with patch.object(renderer, "print_page", return_value=rendered):
            with patch("timiniprint.printing.builder.os.path.isfile", return_value=True):
                jobs = list(PrintJobBuilder(device, document_renderer=renderer).iter_page_jobs("two.pdf"))
    assert len(jobs) == 2
    assert all(job.job.wait_for_completion for job in jobs)


@pytest.mark.parametrize("length", [7, 8, 12])
def test_ff_layout_only_uses_extended_word_for_extended_status(length):
    payload = b"\x04\x00\x01\x80\x09\x03\x19" + b"\x4b" * (length - 7)
    parsed = status_from_frame(next(iter_yk_frames(pack_yk_frame(0xFF, payload))))
    assert parsed.paper_loaded
    assert parsed.extended_flags == (4 if length == 12 else None)
    assert parsed.voltage_raw == (None if length == 12 else 4)
    assert parsed.cutter_ready
    ordinary = status_from_frame(next(iter_yk_frames(status(0x1003))))
    assert not ordinary.paper_loaded


class Connection:
    def __init__(self, controller, *, fail=False, completion="event"):
        self.controller = controller
        self.fail = fail
        self.completion = completion
        self.sent = []
        self.waits = 0

    def can_wait_for_reply(self):
        return True

    async def send_standard_payload(self, packet):
        if self.fail:
            raise OSError("write failed")
        self.sent.extend(iter_yk_frames(packet))
        session = RuntimeConnectionSession(self, reporter=reporting.DUMMY_REPORTER)
        if self.completion == "event":
            self.controller.handle_notification(session, status(command=0x81))
        elif self.completion == "edge":
            self.controller.handle_notification(session, status(0x100B))
            self.controller.handle_notification(session, status())

    async def send(self, job):
        await self.send_standard_payload(job.payload)

    async def wait_for_reply(self, label, match, *, timeout, required=True):
        self.waits += 1
        return None


@pytest.mark.parametrize("path", ["steps", "payload", "fallback"])
@pytest.mark.parametrize("completion", ["event", "edge"])
def test_completion_is_armed_on_every_send_path(path, completion):
    async def run():
        controller = XEasyLabelRuntimeController()
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        plan = PUBLIC_RECIPE.build_job(request(height=1))
        job = ProtocolJob(payload=plan.payload, steps=plan.steps if path != "payload" else (), wait_for_completion=True)
        connection = Connection(controller, completion=completion)
        if path == "fallback":
            connection.send_standard_payload = None
            async def send(job):
                await Connection.send_standard_payload(connection, job.payload)
            connection.send = send
        await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        assert connection.waits == 0
        before = controller.debug_snapshot()["completion_count"]
        controller.handle_notification(RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER), status(command=0x81))
        assert controller.debug_snapshot()["completion_count"] == before
    asyncio.run(run())


def test_shared_sequence_wraps_across_jobs_and_diagnostics():
    async def run():
        controller = XEasyLabelRuntimeController()
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        connection = Connection(controller)
        async def query(packet, *, timeout, reply_complete=None):
            connection.sent.extend(iter_yk_frames(packet))
            return pack_yk_frame(0xF1, b"\x1e\x2c")
        connection.query_control_packet = query
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        assert await controller.query_diagnostic(device, session, "firmware", timeout=0.1) == "44.30"
        job = ProtocolJob(steps=PUBLIC_RECIPE.build_job(request(height=1)).steps)
        for _ in range(12):
            await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        assert [f.sequence for f in connection.sent] == [(i + 1) % 64 for i in range(len(connection.sent))]
    asyncio.run(run())


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("integrity", [False, True])
def test_live_resequencing_keeps_payload_and_integrity_for_single_or_grouped_frames(grouped, integrity):
    async def run():
        controller = XEasyLabelRuntimeController()
        connection = Connection(controller, completion="none")
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        packets = tuple(pack_yk_frame(command, payload, sequence=20 + i, calculated_integrity=integrity)
            for i, (command, payload) in enumerate(((0x0A, b"\x19"), (0x09, b"\x09"), (0, b"\x64\x9b"))))
        steps = (ProtocolStep.send("grouped frames", b"".join(packets)),) if grouped else tuple(
            ProtocolStep.send("frame", packet) for packet in packets)
        assert await controller.send_protocol_steps(session, steps, timeout=0.1)
        assert [frame.sequence for frame in connection.sent] == [1, 2, 3]
        assert [(frame.command, frame.payload) for frame in connection.sent] == [(0x0A, b"\x19"), (0x09, b"\x09"), (0, b"\x64\x9b")]
        assert all(bool(frame.integrity) == integrity for frame in connection.sent)
    asyncio.run(run())


def test_invalid_later_frame_does_not_partially_send_or_advance_sequence():
    async def run():
        controller = XEasyLabelRuntimeController()
        connection = Connection(controller, completion="none")
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        steps = (ProtocolStep.send("valid", pack_yk_frame(0x0A, b"\x19")),
                 ProtocolStep.send("truncated", pack_yk_frame(0, b"\x01")[:-1]))
        assert not await controller.send_protocol_steps(session, steps, timeout=0.1)
        assert connection.sent == []
        assert controller.debug_snapshot()["next_sequence"] == 1
    asyncio.run(run())


def test_status_errors_stop_job_and_failed_send_does_not_leak_completion():
    async def run():
        controller = XEasyLabelRuntimeController(check_cutter=True)
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        connection = Connection(controller, fail=True)
        job = ProtocolJob(steps=PUBLIC_RECIPE.build_job(request(height=1)).steps)
        with pytest.raises(OSError):
            await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        assert not controller.debug_snapshot()["saw_printing"]
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        controller.handle_notification(session, status(0x1803))
        with pytest.raises(PrinterNotReadyError) as error:
            await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        assert error.value.reasons[0].value == "paper_out"
    asyncio.run(run())


def test_paper_transition_queries_before_next_job_and_clears_removed_paper():
    async def run():
        controller = XEasyLabelRuntimeController(paper_query_on_valid=True)
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        connection = Connection(controller, completion="none")
        async def query(packet, *, timeout, reply_complete=None):
            connection.sent.extend(iter_yk_frames(packet))
            reply = pack_yk_frame(0x73, bytes((21, 12, 40, 1)))
            assert reply_complete(reply)
            return reply
        connection.query_control_packet = query
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        controller.handle_notification(session, status())
        controller.handle_notification(session, status(0x3003))
        assert controller.debug_snapshot()["paper_query_pending"]
        assert not connection.sent
        job = ProtocolJob(steps=PUBLIC_RECIPE.build_job(request(height=1)).steps)
        await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        assert connection.sent[0].command == 0x72
        assert controller.debug_snapshot()["paper_details"] == {"paper_type": 21, "width": 12, "length": 40, "color": 1}
        controller.handle_notification(session, status())
        assert controller.debug_snapshot()["paper_details"] is None
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["absent", "malformed", "unavailable", "timeout"])
def test_missing_preflight_response_does_not_prevent_printing(fault):
    async def run():
        controller = XEasyLabelRuntimeController(check_cutter=True)
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        connection = Connection(controller)
        async def query(packet, *, timeout, reply_complete=None):
            if fault == "timeout":
                raise TimeoutError()
            return None if fault == "absent" else b"bad reply"
        if fault != "unavailable":
            connection.query_control_packet = query
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        prepared = await controller.prepare(device, session, timeout=0.01)
        job = ProtocolJob(steps=PUBLIC_RECIPE.build_job(request(height=1)).steps)
        await send_prepared_job(prepared, connection, job)
        assert connection.sent
    asyncio.run(run())


def test_cancelled_send_releases_job_state():
    async def run():
        controller = XEasyLabelRuntimeController()
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        connection = Connection(controller)
        async def send(packet):
            raise asyncio.CancelledError()
        connection.send_standard_payload = send
        job = ProtocolJob(steps=PUBLIC_RECIPE.build_job(request(height=1)).steps)
        with pytest.raises(asyncio.CancelledError):
            await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        assert not controller.debug_snapshot()["job_active"]
        assert not controller.debug_snapshot()["finished"]
    asyncio.run(run())


def test_printer_error_during_completion_is_not_reported_as_timeout():
    async def run():
        controller = XEasyLabelRuntimeController()
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        reporter = Mock(spec=reporting.Reporter)
        connection = Connection(controller, completion="none")
        session = RuntimeConnectionSession(connection, reporter=reporter)
        async def wait(label, match, *, timeout, required=True):
            reply = status(0x1803)
            controller.handle_notification(session, reply)
            assert match(reply)
            return reply
        connection.wait_for_reply = wait
        job = ProtocolJob(steps=PUBLIC_RECIPE.build_job(request(height=1)).steps, wait_for_completion=True)
        with pytest.raises(PrinterNotReadyError):
            await send_prepared_job(PreparedPrinter(device, controller), connection, job, reporter=reporter)
        assert not any(call.kwargs["short"] == "YK Astra P1 completion timeout" for call in reporter.warning.call_args_list)
        assert not controller.debug_snapshot()["job_active"]
    asyncio.run(run())


@pytest.mark.parametrize("query_stage", ["prepare", "paper", "diagnostic"])
def test_coalesced_query_replies_keep_latest_status_and_block_faulty_print(query_stage):
    async def run():
        controller = XEasyLabelRuntimeController(paper_query_on_valid=query_stage == "paper")
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        connection = Connection(controller, completion="none")
        fault = status(0x1803)

        async def query(packet, *, timeout, reply_complete=None):
            command = next(iter_yk_frames(packet)).command
            if query_stage == "paper":
                reply = status(0x3003) if command == 0x10 else pack_yk_frame(0x73, b"\x15\x0c\x28\x01") + fault
            else:
                reply = status() + fault
            assert reply_complete(reply)
            return reply

        connection.query_control_packet = query
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        if query_stage == "diagnostic":
            result = await controller.query_diagnostic(device, session, "status", timeout=0.1)
            assert result["paper_out"]
        else:
            await controller.prepare(device, session, timeout=0.1)
        assert controller.debug_snapshot()["paper_out"]
        job = ProtocolJob(steps=PUBLIC_RECIPE.build_job(request(height=1)).steps)
        with pytest.raises(PrinterNotReadyError):
            await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        assert not connection.sent

    asyncio.run(run())


@pytest.mark.parametrize("path", ["steps", "payload", "fallback"])
@pytest.mark.parametrize("printing_command", [0x80, 0x81, 0xFF])
def test_resumed_printing_invalidates_earlier_completion(path, printing_command):
    async def run():
        controller = XEasyLabelRuntimeController()
        device = PrinterCatalog.load().device_from_model("xeasylabel_hp6_c")
        connection = Connection(controller, completion="none")
        session = RuntimeConnectionSession(connection, reporter=reporting.DUMMY_REPORTER)
        plan = PUBLIC_RECIPE.build_job(request(height=1))
        job = ProtocolJob(payload=plan.payload, steps=plan.steps if path != "payload" else (), wait_for_completion=True)

        async def send(packet):
            # An earlier image/feed can finish while the rest of the job is queued.
            controller.handle_notification(session, status(command=0x81))
            controller.handle_notification(session, status(0x100B, command=printing_command))

        async def wait(label, match, *, timeout, required=True):
            connection.waits += 1
            assert not match(status(command=0x81) + status(0x100B, command=printing_command))
            reply = status(command=0x81)
            assert match(reply)
            return reply

        connection.send_standard_payload = send
        if path == "fallback":
            connection.send_standard_payload = None
            async def send_job(job):
                await send(job.payload)
            connection.send = send_job
        connection.wait_for_reply = wait
        await send_prepared_job(PreparedPrinter(device, controller), connection, job)
        assert connection.waits == 1
        assert not controller.debug_snapshot()["finished"]

    asyncio.run(run())
