from dataclasses import replace
from unittest.mock import patch

from timiniprint.devices import PrinterCatalog
from timiniprint.protocol import PrinterProtocol
from timiniprint.protocol.plan import ProtocolPlan
from timiniprint.raster import PixelFormat, RasterBuffer, RasterSet


def test_recipe_receives_resolved_image_energy_and_paper_offset():
    device = PrinterCatalog.load().device_from_model("x5")
    energy = replace(
        device.profile.energy,
        image=replace(device.profile.energy.image, middle=1234),
        text=replace(device.profile.energy.text, middle=5678),
    )
    profile = replace(
        device.profile, back_paper_num=7,
        print_defaults=replace(device.profile.print_defaults, energy=energy),
    )
    device = replace(device, profile=profile)
    raster = RasterSet.from_single(RasterBuffer([0] * 384, 384, PixelFormat.BW1))
    with patch("timiniprint.protocol._builders._build_family_job", return_value=ProtocolPlan.stream(b"job")) as build:
        PrinterProtocol(device).build_job(raster, is_text=True)
    request = build.call_args[0][0]
    assert request.energy == 5678
    assert request.image_energy == 1234
    assert request.back_paper_num == 7
