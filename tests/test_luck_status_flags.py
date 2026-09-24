import pytest

from tests.test_luck_transactions import Connection, page, send
from timiniprint.devices import PrinterCatalog
from timiniprint.printing import PrinterNotReadyError
from timiniprint.protocol import PrinterStatusCode as Status


@pytest.fixture(scope="module")
def device():
    return PrinterCatalog.load().device_from_profile("luck_a40")


@pytest.mark.parametrize("flags", range(256))
def test_normal_status_preserves_all_faults_and_ignores_charging(device, flags):
    expected = tuple(code for mask, code in (
        (0x02, Status.COVER_OPEN), (0x04, Status.PAPER_OUT),
        (0x08, Status.LOW_BATTERY), (0x50, Status.OVERHEATED), (0x01, Status.BUSY),
    ) if flags & mask)
    conn = Connection(overrides={b"\x10\xff\x40": bytes([flags])})
    if expected:
        with pytest.raises(PrinterNotReadyError) as caught:
            send(device, page(device), conn)
        assert caught.value.reasons == expected
        assert not any(event[0] == "send" for event in conn.events)
    else:
        send(device, page(device), conn)
        assert any(event[0] == "send" for event in conn.events)
