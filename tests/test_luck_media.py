import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.settings import PrintSettings
from timiniprint.protocol import PaperMode, PrinterProtocol, ProtocolStepOperation
from tests.test_luck_profiles import raster
from tests.test_luck_transactions import Connection, send


@pytest.fixture(scope="module")
def catalog():
    return PrinterCatalog.load()


def assert_compact_media(device, width, feed, *, lujiang, enable=3):
    """Check presets and full page order through the public builder."""
    protocol = PrinterProtocol(device)
    for mode, preset in ((PaperMode.BLACK_TAG, f"blacktag_{width}r"),
                         (PaperMode.TATTOO, f"tattoo_{width}r")):
        paper = next(p for p in device.profile.paper_presets if p.key == preset)
        assert paper.paper_mode is mode
        assert paper.paper_width_px == paper.render_width_px == width
        assert paper.render_height_px is None
        assert not paper.left_padding_px and not paper.top_padding_px
        assert not paper.rotation_degrees and not paper.mirror_horizontal
        PrintSettings(paper_preset_key=preset).resolve_image_pipeline(device)
        for index in (1, 2, 3):
            job = protocol.build_job(raster(width), is_text=False,
                                     paper_preset_key=preset, page_index=index, page_count=3)
            expected = ["density", "status", "enable", "wakeup"]
            selects_paper = lujiang or mode is PaperMode.TATTOO
            if selects_paper:
                expected.append("paper type")
            expected.extend(["bitmap", "line feed" if mode is PaperMode.TATTOO else "position"])
            if lujiang and mode is PaperMode.BLACK_TAG and index == 3:
                expected.append("mark last")
            expected.append("finalize")
            assert [s.label for s in job.steps] == expected
            assert job.steps[2].data == bytes([0x10, 0xFF, 0xF1, enable])
            if selects_paper:
                step = next(s for s in job.steps if s.label == "paper type")
                assert step.data == bytes([0x1F, 0x80, 1, 0x40 if mode is PaperMode.TATTOO else 0x50])
                assert step.operation is ProtocolStepOperation.QUERY
                assert step.timeout_sec == 3 and not step.reply_required
            if mode is PaperMode.TATTOO:
                assert job.steps[-2].data == bytes([0x1B, 0x4A, feed])
            else:
                assert next(s for s in job.steps if s.label == "position").data == bytes.fromhex("1d 0c")
            assert job.steps[-1].timeout_sec == 70
            assert job.steps[-1].reply_required


@pytest.mark.parametrize("name,key,width,feed,lujiang,enable,default", [
    ("PPA2_1234", "luck_a2", 384, 80, False, 3, "plain_384r"),
    ("PPA2H_1234", "luck_a2h", 576, 120, False, 3, "plain_576r"),
    ("PPA2L_1234", "luck_ppa2l", 384, 80, True, 3, "tag_384r"),
    ("PPA2LH_1234", "luck_ppa2lh", 576, 120, True, 3, "tag_576r"),
    ("QIRUI_Q1_1234", "luck_qirui_q1", 384, 80, False, 2, "plain_384r"),
    ("QIRUI_Q2_1234", "luck_qirui_q2", 576, 130, False, 2, "plain_576r"),
])
def test_detected_compact_profiles_offer_exact_media_recipes(catalog, name, key, width, feed, lujiang, enable, default):
    device = catalog.detect_device(name)
    assert device.profile.profile_key == key
    assert device.profile.default_paper_preset.key == default
    assert device.profile.use_spp
    assert_compact_media(device, width, feed, lujiang=lujiang, enable=enable)


def test_d80_uses_only_the_defined_local_tattoo_recipe(catalog):
    device = catalog.device_from_model("luck_d80")
    assert device.profile.default_paper_preset.key == "luck_a4_roll_216mm"
    job = PrinterProtocol(device).build_job(raster(1648), is_text=False, paper_preset_key="tattoo_1648r")
    assert [s.label for s in job.steps] == [
        "density", "status", "enable", "wakeup", "paper type", "bitmap", "line feed", "finalize",
    ]
    assert job.steps[4].data == bytes.fromhex("1f 80 01 40")
    assert job.steps[5].data.startswith(bytes.fromhex("1f 10 00 ce"))
    assert job.steps[6].data == bytes.fromhex("1b 4a 90")
    assert not any(s.data.startswith(bytes.fromhex("1f 70 01")) for s in job.steps)
    assert not any(s.data == bytes.fromhex("10 ff 20 f2") for s in job.steps)


@pytest.mark.parametrize("key,mode", [("luck_a2", PaperMode.BLACK_TAG), ("luck_a2", PaperMode.TATTOO),
    ("luck_ppa2l", PaperMode.BLACK_TAG), ("luck_ppa2l", PaperMode.TATTOO), ("luck_d80", PaperMode.TATTOO)])
@pytest.mark.parametrize("notifications", [False, True])
def test_added_media_execute_existing_reply_contracts(catalog, key, mode, notifications):
    device = catalog.device_from_profile(key)
    job = PrinterProtocol(device).build_job(raster(), is_text=False, paper_mode=mode)
    # Optional paper ACK still gets a wait; its absence must not skip completion.
    conn = Connection(notifications=notifications, overrides={
        bytes.fromhex("1f 80 01 40"): None, bytes.fromhex("1f 80 01 50"): None,
    })
    send(device, job, conn)
    queries = [(e[1], e[2]) for e in conn.events if e[0] == "query"]
    assert queries == [(s.data, s.timeout_sec) for s in job.steps if s.operation is ProtocolStepOperation.QUERY]
    assert conn.events[-1] == ("query", bytes.fromhex("10 ff f1 45"), 70)
