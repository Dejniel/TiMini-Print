import pytest

from timiniprint.protocol.commands import advance_paper_cmd, retract_paper_cmd
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.packet import make_packet


@pytest.mark.parametrize("family", [ProtocolFamily.TINY, ProtocolFamily.TINY_PREFIXED, ProtocolFamily.V5G])
def test_common_paper_commands_are_limited_to_their_dialects(family):
    assert advance_paper_cmd(203, family) == make_packet(0xA1, b"\x30\x00", family)
    assert retract_paper_cmd(300, family) == make_packet(0xA0, b"\x48\x00", family)


@pytest.mark.parametrize("family", [ProtocolFamily.V5C, ProtocolFamily.DCK, ProtocolFamily.ELEPH_TSPL])
@pytest.mark.parametrize("command", [advance_paper_cmd, retract_paper_cmd])
def test_missing_motion_builder_does_not_invent_a_prefixed_command(family, command):
    with pytest.raises(NotImplementedError, match="does not implement manual"):
        command(203, family)
