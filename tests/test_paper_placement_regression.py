"""Wire-level regression vectors captured before normalizing paper placement."""
import hashlib
import json

import pytest

from timiniprint.devices import PrinterCatalog
from timiniprint.printing.raster_job import build_raster_job
from timiniprint.printing.settings import PrintSettings
from timiniprint.raster import RasterBuffer, RasterSet

CASES = [
    ("15p3", "default_90r_96p", 40, "76f97348b0143f912477226c4ca76b0576ac2117a17abb20f85845554556ffc4"),
    ("15p3", "default_90r_96p", 90, "5d9da16bc7df908009e9fbb8ab5405b2c730930fdbc743d0515660559f3ad64c"),
    ("a200", "plain_1648r_1664p", 1648, "61bb78b224f9558cc3da53fabbb5a2eb75edc3f38715e4afb887c5329d5c1606"),
    ("a200", "plain_1648r_1664p", 824, "f4e6604e05221ad4857aeae18a8061085c43a804b042b7b720137c21ff72cecc"),
]


@pytest.mark.parametrize("profile,paper,width,expected", CASES)
def test_existing_horizontal_layout_keeps_payload_and_step_order(profile, paper, width, expected):
    device = PrinterCatalog.load().device_from_profile(profile)
    raster = RasterSet.from_single(RasterBuffer(
        [int((x + 3 * y) % 19 == 0 or x == 1) for y in range(3) for x in range(width)], width))
    job = build_raster_job(device, raster, is_text=False,
                           settings=PrintSettings(paper_preset_key=paper, feed_padding=0))
    data = {"payload": job.payload.hex(), "steps": [
        (step.operation.value, step.label, step.data.hex(), step.expect.value, step.reply_required)
        for step in job.steps], "repeats": getattr(job, "repeat_count", 1),
        "completion": job.wait_for_completion}
    assert hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest() == expected
