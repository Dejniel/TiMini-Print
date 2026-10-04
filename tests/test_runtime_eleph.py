import asyncio
import zlib
from dataclasses import replace

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.device import SerialTarget
from timiniprint.printing.runtime.eleph import ElephRuntimeController
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.protocol import PrinterProtocol
from timiniprint.protocol.families.eleph_control import DEVICE_INFO_QUERY, STATUS_QUERY
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.types import ImageEncoding
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


def information(*, width=576, dpi_type=1, mode=2, compressed=False, paper_type=1):
    data = bytearray(116)
    data[4:6] = (116).to_bytes(2, "little")
    data[6:8] = (6042).to_bytes(2, "little")
    data[8:10] = width.to_bytes(2, "little")
    data[10], data[11], data[14], data[15] = dpi_type, mode, 4, 10
    data[17], data[22] = paper_type, int(compressed)
    return bytes(data)


class Session:
    def __init__(self, response, *, ble=False, query_available=True, timeout=False):
        self.response = response
        self.ble = ble
        self.query_available = query_available
        self.timeout = timeout
        self.sent = []
        self.debug = []
        self.warnings = []
        self.attached = None

    async def attach_runtime_controller(self, controller, *, timeout):
        self.attached = controller

    def can_query_control_packet(self):
        return self.query_available and not self.ble

    def can_send_control_packet_wait_notification(self):
        return self.query_available and self.ble

    def report_debug(self, message):
        self.debug.append(message)

    def report_warning(self, **warning):
        self.warnings.append(warning)

    async def _query(self, packet, complete):
        assert packet in (DEVICE_INFO_QUERY, STATUS_QUERY)
        self.sent.append(packet)
        if self.timeout:
            raise TimeoutError("no device information")
        buffer = bytearray()
        response = self.response.get(packet) if isinstance(self.response, dict) else self.response
        for value in response or b"":
            fragment = bytes([value])
            buffer.extend(fragment)
            if self.attached is not None:
                self.attached.handle_notification(self, fragment)
            candidate = fragment if self.ble else bytes(buffer)
            if complete(candidate):
                return candidate
        return None if self.ble else bytes(buffer) or None

    async def query_control_packet(self, packet, *, timeout, reply_complete):
        assert not self.ble
        return await self._query(packet, reply_complete)

    async def send_control_packet_wait_notification(self, packet, *, label, match, timeout, required):
        assert self.ble and not required
        return await self._query(packet, match)


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("dpi_type,dpi,dots_mm", [(0, 203, 8), (1, 305, 12), (2, 610, 24)])
@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("mode", [1, 2])
def test_preparation_resolves_settings_from_fragmented_info_without_mutation(ble, dpi_type, dpi, dots_mm, compressed, mode):
    width = 48 * dots_mm
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    device = device.with_transport_target(SerialTarget("/dev/eleph-test"))
    device = replace(device, profile=replace(device.profile, use_spp=not ble))
    session = Session(information(width=width, dpi_type=dpi_type, compressed=compressed, mode=mode), ble=ble)
    prepared = asyncio.run(prepare_connection_runtime(device, session, timeout=1))
    resolved = prepared.device

    assert isinstance(prepared.runtime_controller, ElephRuntimeController)
    assert session.attached is prepared.runtime_controller
    assert session.sent == [DEVICE_INFO_QUERY]
    assert resolved.profile.dev_dpi == dpi
    assert resolved.profile.default_paper_preset.render_width_px == width
    assert resolved.profile.default_paper_preset.paper_width_px == width
    assert device.profile.default_paper_preset.render_width_px == 384
    assert device.profile.select_speed(is_text=False) is None
    assert resolved.profile.select_speed(is_text=False) == 4
    assert resolved.profile.select_density(is_text=False, blackening=3) == 10
    assert resolved.model_key == device.model_key
    assert resolved.profile_key == device.profile_key
    assert resolved.transport_target == device.transport_target
    assert resolved.profile.stream == device.profile.stream
    assert resolved.profile.use_spp == device.profile.use_spp
    assert resolved.ble_transport_profile == device.ble_transport_profile
    assert resolved.image_pipeline == resolved.profile.default_image_pipeline
    family = ProtocolFamily.ELEPH_ESC if mode == 1 else ProtocolFamily.ELEPH_TSPL
    assert resolved.protocol_family is family
    assert resolved.profile.protocol_default.type is family
    if mode == 1:
        encoding = ImageEncoding.ELEPH_ESC_ZLIB if compressed else ImageEncoding.ELEPH_ESC_RAW
    else:
        encoding = ImageEncoding.ELEPH_TSPL_ZLIB if compressed else ImageEncoding.ELEPH_TSPL_BITMAP
    assert resolved.image_pipeline.encoding is encoding
    raster = RasterBuffer([0] * (width * dots_mm), width, PixelFormat.BW1)
    job = PrinterProtocol(resolved).build_job(RasterSet.from_single(raster), is_text=False)
    if mode == 2:
        assert job.payload.startswith(b"SIZE 48 mm,1 mm\nSPEED 04\nDENSITY 10\nCLS\nDIRECTION 0\n")
    else:
        assert job.payload.startswith(b"\x1b@\x1ba\x01")
        assert job.payload.endswith(b"\x1d\x0c")
    assert b"GAP " not in job.payload
    assert not job.steps


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("length", [0, 4, 84, 115])
@pytest.mark.parametrize("family", [ProtocolFamily.ELEPH_TSPL, ProtocolFamily.ELEPH_ESC])
def test_missing_or_short_information_keeps_explicit_profile(ble, length, family):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    if family is ProtocolFamily.ELEPH_ESC:
        device = replace(device, protocol_family=family).with_print_profile(
            device.profile,
            image_pipeline=replace(device.image_pipeline, encoding=ImageEncoding.ELEPH_ESC_RAW),
        )
    controller = ElephRuntimeController()
    session = Session(information()[:length], ble=ble)
    prepared = asyncio.run(controller.prepare(device, session, timeout=1))
    assert prepared.device is device
    assert session.warnings
    raster = RasterBuffer([0] * (384 * 8), 384, PixelFormat.BW1)
    job = PrinterProtocol(prepared.device).build_job(RasterSet.from_single(raster), is_text=False)
    if family is ProtocolFamily.ELEPH_TSPL:
        assert job.payload.startswith(b"SIZE 48 mm,1 mm\nCLS\nDIRECTION 0\n")
    else:
        assert job.payload.startswith(b"\x1b@\x1ba\x01\x1d\x76\x30\x30")


@pytest.mark.parametrize("ble", [False, True])
def test_optional_information_timeout_keeps_explicit_profile(ble):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    session = Session(None, ble=ble, timeout=True)
    prepared = asyncio.run(ElephRuntimeController().prepare(device, session, timeout=1))
    assert prepared.device is device
    assert session.warnings


def test_send_only_connection_can_prepare_without_queries():
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    session = Session(None, query_available=False)
    prepared = asyncio.run(prepare_connection_runtime(device, session))
    assert prepared.device is device
    assert session.sent == []


@pytest.mark.parametrize("ble", [False, True])
@pytest.mark.parametrize("mode", [0, 3, 255])
def test_unknown_command_mode_is_not_silently_printed_or_changed(ble, mode):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    session = Session(information(mode=mode), ble=ble)
    with pytest.raises(ValueError, match="unknown command mode"):
        asyncio.run(prepare_connection_runtime(device, session))
    assert session.sent == [DEVICE_INFO_QUERY]


@pytest.mark.parametrize("fields,error", [({"width": 0}, "width"), ({"dpi_type": 3}, "DPI")])
def test_invalid_confirmed_geometry_is_rejected(fields, error):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    with pytest.raises(ValueError, match=error):
        asyncio.run(prepare_connection_runtime(device, Session(information(**fields))))


@pytest.mark.parametrize("dpi,dots_mm", [(200, 8), (203, 8), (300, 12), (305, 12), (600, 24), (610, 24)])
def test_whole_mm_geometry_does_not_truncate_from_rounded_dpi(dpi, dots_mm):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    width = 48 * dots_mm
    paper = replace(device.profile.default_paper_preset, paper_width_px=width, render_width_px=width)
    device = device.with_print_profile(replace(device.profile, dev_dpi=dpi, paper_presets=(paper,)))
    raster = RasterBuffer([0] * (width * dots_mm), width, PixelFormat.BW1)
    job = PrinterProtocol(device).build_job(RasterSet.from_single(raster), is_text=False)
    assert job.payload.startswith(b"SIZE 48 mm,1 mm\n")


@pytest.mark.parametrize("paper_type", [0, 1, 2, 3])
def test_information_read_does_not_write_media_or_tear_settings(paper_type):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    session = Session(information(paper_type=paper_type))
    prepared = asyncio.run(prepare_connection_runtime(device, session))
    assert session.sent == [b"\x1b##JXIG"]
    raster = RasterBuffer([0] * (576 * 12), 576, PixelFormat.BW1)
    job = PrinterProtocol(prepared.device).build_job(RasterSet.from_single(raster), is_text=False)
    assert b"GAP " not in job.payload
    assert b"\x1b##JXIS" not in job.payload


@pytest.mark.parametrize("known_profile", [False, True])
def test_head_width_does_not_replace_custom_label_format(known_profile):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    paper = replace(device.profile.default_paper_preset, paper_width_px=400, render_width_px=400,
                    render_height_px=480, left_padding_px=0)
    profile = replace(device.profile, paper_presets=(paper,))
    if not known_profile:
        profile = replace(profile, profile_key="manual_eleph")
    device = device.with_print_profile(profile)
    session = Session(information(width=576, dpi_type=0))
    prepared = asyncio.run(prepare_connection_runtime(device, session))
    assert prepared.device.profile.default_paper_preset == paper
    assert prepared.device.profile.default_paper_preset.render_height_px == 480


def test_custom_label_wider_than_confirmed_head_is_rejected():
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    paper = replace(device.profile.default_paper_preset, paper_width_px=400, render_width_px=400)
    device = device.with_print_profile(replace(device.profile, paper_presets=(paper,)))
    with pytest.raises(ValueError, match="paper exceeds.*width"):
        asyncio.run(prepare_connection_runtime(device, Session(information(width=384, dpi_type=0))))


@pytest.mark.parametrize("compressed", [False, True])
def test_asymmetric_bitmap_vector_preserves_padding_and_binary_payload(compressed):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    coordinates = ((0, 0), (2, 0), (8, 0), (3, 1), (9, 1))
    pixels = [0] * 20
    for x, y in coordinates:
        pixels[y * 10 + x] = 1
    raster = RasterBuffer(pixels, 10, PixelFormat.BW1)
    encoding = ImageEncoding.ELEPH_TSPL_ZLIB if compressed else ImageEncoding.ELEPH_TSPL_BITMAP
    job = PrinterProtocol(device).build_job(
        RasterSet.from_single(raster), is_text=False, image_encoding_override=encoding,
    )
    if compressed:
        marker = b"BITMAP 0,0,2,2,3,12,"
        offset = job.payload.index(marker) + len(marker)
        body = job.payload[offset:offset + 12]
        assert zlib.decompress(body) == b"\xa0\x80\x10\x40"
        assert job.payload[offset + 12:] == b"\nPRINT 1,1\n"
    else:
        assert job.payload.endswith(b"BITMAP 0,0,2,2,0,\x5f\x7f\xef\xbf\nPRINT 1,1\n")



@pytest.mark.parametrize("initial_mode,reported_mode", [(1, 2), (2, 1)])
def test_family_switch_returns_new_prepared_controller(initial_mode, reported_mode):
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    if initial_mode == 1:
        device = replace(device, protocol_family=ProtocolFamily.ELEPH_ESC).with_print_profile(
            device.profile,
            image_pipeline=replace(device.image_pipeline, encoding=ImageEncoding.ELEPH_ESC_RAW),
        )
    bootstrap = ElephRuntimeController()
    session = Session(information(mode=reported_mode))
    prepared = asyncio.run(prepare_connection_runtime(device, session, controller=bootstrap))
    assert prepared.runtime_controller is not bootstrap
    assert session.attached is prepared.runtime_controller
    assert prepared.runtime_controller.debug_snapshot()["device_info"]["command_mode"] == reported_mode
    assert device.protocol_family is (ProtocolFamily.ELEPH_ESC if initial_mode == 1 else ProtocolFamily.ELEPH_TSPL)


@pytest.mark.parametrize("ble", [False, True])
def test_query_information_text_is_not_an_unsolicited_error(ble):
    response = bytearray(information())
    response[32:40] = b"err:\x20\x48\x0c\x00"
    session = Session(bytes(response), ble=ble)
    prepared = asyncio.run(prepare_connection_runtime(
        PrinterCatalog.load().device_from_model("eleph_tspl_p1"), session,
    ))
    assert prepared.runtime_controller.debug_snapshot()["status"] is None


def test_bitmap_lf_is_not_treated_as_a_command_delimiter():
    device = PrinterCatalog.load().device_from_model("eleph_tspl_p1")
    raster = RasterBuffer([int(not (0x0a & (1 << bit))) for bit in range(7, -1, -1)], 8, PixelFormat.BW1)
    job = PrinterProtocol(device).build_job(RasterSet.from_single(raster), is_text=False)
    assert job.payload.endswith(b"BITMAP 0,0,1,1,0,\x0a\nPRINT 1,1\n")
