import asyncio
from dataclasses import replace

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.profiles import LevelProfile, SpeedProfile
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.printing.send import send_prepared_job
from timiniprint.printing.step_execution import ProtocolReplyError
from timiniprint.protocol import PrinterProtocol
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet
from tests.test_luck_transactions import Connection
from tests.test_runtime_luck_normal import _ConnectionReporter


VERSION = bytes.fromhex("10 ff 20 f1")
SPEED = bytes.fromhex("10 ff c0 04")


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


def prepare(device, connection, **kwargs):
    return asyncio.run(prepare_connection_runtime(device, connection, **kwargs))


def page(device, *, blackening=3, is_text=False):
    raster = RasterSet.from_single(RasterBuffer([1] * 8, 8, PixelFormat.BW1))
    return PrinterProtocol(device).build_job(raster, is_text=is_text, blackening=blackening)


@pytest.mark.parametrize("notifications", [False, True])
@pytest.mark.parametrize("reply,levels,speed", [
    (b"1.25", (0, 1, 2), None),
    (b"V1.25", (0, 1, 2), None),
    (b"1.100", (0, 1, 2), None),  # Text comparison, deliberately not semver.
    (b"\x00 \r\n", (0, 1, 2), None),
    (b"1.26", (1, 8, 15), 4),
    (b"V1.26", (1, 8, 15), 4),
    (b" \x00v1.26\r\n\x00", (1, 8, 15), 4),
    (b"v1.v26", (1, 8, 15), 4),
    (b"1.3", (1, 8, 15), 4),
    (None, (0, 7, 15), None),
])
def test_preparation_negotiates_immutable_defaults_and_job_packets(catalog, notifications, reply, levels, speed):
    device = catalog.detect_device("LuckP_A41_1234", "AA:BB:CC:DD:EE:01")
    assert device is not None
    assert device.profile.profile_key == "luck_a41_luckp"
    original = device.profile
    reporter = _ConnectionReporter()
    conn = Connection(notifications=notifications, overrides={VERSION: reply})
    prepared = prepare(device, conn, timeout=10, reporter=reporter)

    assert device.profile is original
    assert original.density.image == original.density.text == LevelProfile(0, 7, 15)
    assert original.speed is None
    assert prepared.device.profile.density.image == prepared.device.profile.density.text == LevelProfile(*levels)
    assert prepared.device.profile.speed == (None if speed is None else SpeedProfile(speed, speed))
    assert prepared.device.transport_target == device.transport_target
    assert prepared.device.profile.stream == original.stream
    assert prepared.device.protocol_variant == device.protocol_variant
    assert prepared.device.profile.paper_presets == original.paper_presets
    assert [event for event in conn.events if event[0] == "query"] == [("query", VERSION, 3)]
    assert bool(reporter.warnings) == (reply is None)
    if notifications:
        assert conn.events[0] == ("arm", "firmware")

    for is_text in (False, True):
        for blackening, density in zip((1, 3, 5), levels):
            job = page(prepared.device, blackening=blackening, is_text=is_text)
            assert job.steps[0].label == "density"
            assert job.steps[0].data == bytes([0x10, 0xFF, 0x10, 0x00, density])
            speed_steps = [step for step in job.steps if step.label == "speed"]
            assert len(speed_steps) == (speed is not None)
            if speed is not None:
                assert [step.label for step in job.steps[:3]] == ["density", "speed", "status"]
                assert speed_steps[0].data == SPEED
                assert speed_steps[0].timeout_sec == 3
                assert speed_steps[0].reply_required

    job = page(prepared.device)
    asyncio.run(send_prepared_job(prepared, conn, job))
    assert sum(event[:2] == ("query", VERSION) for event in conn.events) == 1
    assert conn.events[-1] == ("query", bytes.fromhex("10 ff f1 45"), 70)


def test_successful_empty_firmware_uses_legacy_limits(catalog):
    prepared = prepare(catalog.device_from_profile("luck_a41_luckp"), Connection(overrides={VERSION: b""}))
    assert prepared.device.profile.density.image == LevelProfile(0, 1, 2)
    assert prepared.device.profile.speed is None


def test_shorter_preparation_timeout_is_honored(catalog):
    conn = Connection(overrides={VERSION: b"1.26"})
    prepare(catalog.device_from_profile("luck_a41_luckp"), conn, timeout=0.25)
    assert conn.events == [("query", VERSION, 0.25)]


def test_query_errors_do_not_hide_a_disconnect(catalog):
    class FailedConnection(Connection):
        failure = TimeoutError

        async def query_control_packet(self, *args, **kwargs):
            raise self.failure("query failed")

    device = catalog.device_from_profile("luck_a41_luckp")
    conn = FailedConnection()
    reporter = _ConnectionReporter()
    prepared = prepare(device, conn, reporter=reporter)
    assert prepared.device.profile.density.image == LevelProfile(0, 7, 15)
    assert prepared.device.profile.speed is None
    assert reporter.warnings
    conn.failure = ConnectionError
    with pytest.raises(ConnectionError):
        prepare(device, conn)


def test_reconnections_reset_prior_controls_even_when_reusing_prepared_device(catalog):
    device = catalog.device_from_profile("luck_a41_luckp")
    prepared = prepare(device, Connection(overrides={VERSION: b"1.26"}))
    controller = prepared.runtime_controller
    for connection, levels in (
        (Connection(overrides={VERSION: b"1.25"}), LevelProfile(0, 1, 2)),
        (Connection(overrides={VERSION: None}), LevelProfile(0, 7, 15)),
        (Connection(replies_available=False), LevelProfile(0, 7, 15)),
    ):
        resolved = prepare(prepared.device, connection, controller=controller)
        assert resolved.device.profile.density.image == levels
        assert resolved.device.profile.speed is None


@pytest.mark.parametrize("notifications", [False, True])
@pytest.mark.parametrize("reply,accepted", [(b"OK", True), (b"OKextra", True),
    (None, False), (b"NO", False), (b"\x00OK", False)])
def test_speed_ack_is_required_before_bitmap(catalog, notifications, reply, accepted):
    conn = Connection(notifications=notifications, overrides={VERSION: b"1.26", SPEED: reply})
    prepared = prepare(catalog.device_from_profile("luck_a41_luckp"), conn)
    job = page(prepared.device)
    if accepted:
        asyncio.run(send_prepared_job(prepared, conn, job))
        assert any(event[0] == "send" for event in conn.events)
    else:
        with pytest.raises(ProtocolReplyError) as error:
            asyncio.run(send_prepared_job(prepared, conn, job))
        assert error.value.step.label == "speed"
        assert all(event[0] != "send" for event in conn.events)


@pytest.mark.parametrize("image_speed,text_speed", [(0, 8), (-1, 4), (4, 9)])
def test_explicit_speed_profile_is_validated_per_print_mode(catalog, image_speed, text_speed):
    device = catalog.device_from_profile("luck_a41_luckp")
    defaults = replace(device.profile.print_defaults, speed=SpeedProfile(image_speed, text_speed))
    device = device.with_print_profile(replace(device.profile, print_defaults=defaults))
    prepared = prepare(device, Connection(overrides={VERSION: b"1.26"}))
    for is_text, speed in ((False, image_speed), (True, text_speed)):
        if 0 <= speed <= 8:
            job = page(prepared.device, is_text=is_text)
            assert job.steps[1].data == bytes([0x10, 0xFF, 0xC0, speed])
        else:
            with pytest.raises(ValueError, match="Luck speed must be in 0..8"):
                page(prepared.device, is_text=is_text)


@pytest.mark.parametrize("key", ["luck_a42_luckp", "luck_a40", "luck_lujiang_a4"])
def test_other_a4_variants_do_not_probe_or_send_speed(catalog, key):
    device = catalog.device_from_profile(key)
    defaults = replace(device.profile.print_defaults, speed=SpeedProfile(4, 4))
    device = device.with_print_profile(replace(device.profile, print_defaults=defaults))
    conn = Connection()
    prepared = prepare(device, conn)
    assert conn.events == []
    assert prepared.device is device
    assert not any(step.label == "speed" for step in page(prepared.device).steps)


def test_unprepared_a41_retains_initial_density_and_no_speed(catalog):
    device = catalog.device_from_profile("luck_a41_luckp")
    job = page(device)
    assert job.steps[0].data == bytes.fromhex("10 ff 10 00 07")
    assert not any(step.label == "speed" for step in job.steps)
