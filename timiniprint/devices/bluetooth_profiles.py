from __future__ import annotations

from dataclasses import dataclass

from ..protocol.family import ProtocolFamily


@dataclass(frozen=True)
class BleBulkWriteProfile:
    char_uuid: str
    chunk_cap: int = 20
    write_delay_ms: int = 50
    flow_controlled: bool = False


@dataclass(frozen=True)
class BleTransportProfile:
    standard_chunk_cap: int = 20
    standard_write_delay_ms: int = 50
    preferred_service_uuid: str = ""
    preferred_write_char_uuid: str = ""
    notify_char_uuid: str = ""
    prefer_generic_notify: bool = False
    flow_controlled_standard_write: bool = False
    # Seconds to wait for a resume frame before giving up. None keeps the send
    # timeout, which is right for a link that should answer promptly; printers
    # that pause to drain a print buffer need far longer than that.
    flow_resume_timeout_s: float | None = None
    bulk_write: BleBulkWriteProfile | None = None
    # Some BLE writers need a smaller application chunk than the reported ATT
    # payload for write-without-response transfers.
    write_without_response_payload_reserve: int = 0


_FALLBACK_PROFILE = BleTransportProfile()

_PROFILES = {
    ProtocolFamily.TINY: BleTransportProfile(standard_chunk_cap=512),
    ProtocolFamily.TINY_PREFIXED: BleTransportProfile(standard_chunk_cap=512),
    ProtocolFamily.V5G: BleTransportProfile(
        prefer_generic_notify=True,
        standard_chunk_cap=56 * 8,
        standard_write_delay_ms=30,
        write_without_response_payload_reserve=5,
    ),
    ProtocolFamily.V5C: BleTransportProfile(
        prefer_generic_notify=True,
        flow_controlled_standard_write=True,
    ),
    ProtocolFamily.V5X: BleTransportProfile(
        preferred_service_uuid="0000ae30-0000-1000-8000-00805f9b34fb",
        notify_char_uuid="0000ae02-0000-1000-8000-00805f9b34fb",
        bulk_write=BleBulkWriteProfile(
            char_uuid="0000ae03-0000-1000-8000-00805f9b34fb",
            chunk_cap=180,
            write_delay_ms=30,
            flow_controlled=True,
        ),
        write_without_response_payload_reserve=5,
    ),
    ProtocolFamily.PHOMEMO_ESC: BleTransportProfile(
        preferred_service_uuid="0000ff00-0000-1000-8000-00805f9b34fb",
        preferred_write_char_uuid="0000ff02-0000-1000-8000-00805f9b34fb",
        standard_chunk_cap=128,
        standard_write_delay_ms=20,
    ),
    ProtocolFamily.NIIMBOT: BleTransportProfile(
        preferred_service_uuid="e7810a71-73ae-499d-8c15-faa9aef0c3f2",
        prefer_generic_notify=True,
        standard_chunk_cap=20,
        standard_write_delay_ms=10,
    ),
    ProtocolFamily.ELEPH_HPRT_ESC: BleTransportProfile(
        standard_chunk_cap=180,
        standard_write_delay_ms=10,
    ),
    ProtocolFamily.ELEPH_TSPL: BleTransportProfile(
        preferred_service_uuid="000018f0-0000-1000-8000-00805f9b34fb",
        preferred_write_char_uuid="00002af1-0000-1000-8000-00805f9b34fb",
        standard_chunk_cap=180,
        standard_write_delay_ms=10,
    ),
    ProtocolFamily.INSTAPRINT_CORE: BleTransportProfile(
        standard_chunk_cap=180,
        standard_write_delay_ms=10,
    ),
    ProtocolFamily.FUNNY_LX: BleTransportProfile(
        preferred_service_uuid="0000ffe6-0000-1000-8000-00805f9b34fb",
        preferred_write_char_uuid="0000ffe1-0000-1000-8000-00805f9b34fb",
        notify_char_uuid="0000ffe2-0000-1000-8000-00805f9b34fb",
        standard_chunk_cap=100,
        standard_write_delay_ms=0,
    ),
}

DEFAULT_BLE_TRANSPORT_PROFILE = _PROFILES[ProtocolFamily.TINY]

# Transport behaviour selected by runtime control algorithm rather than family.
# Standard writes are write-without-response, so the transport has no
# back-pressure of its own: it pushes a whole job out in seconds while the
# printer needs minutes, and whatever does not fit is dropped silently. A device
# that reports when it cannot take more can gate the write loop instead.
#
# Opt-in per device, not per family: one tested unit says nothing about the rest
# of a family, and subscribing to notifications changes behaviour for every
# device that would resolve to it.
_CONTROL_ALGORITHM_PROFILES = {
    # Verified on an X5 (advertised name "X5", profile x5_2, BLE). It sends the
    # tiny pause/resume frames on a generic notify characteristic. Measured with
    # a 54800-byte job: it paused twice and sent the resume frame after 35.8s
    # and 30.2s, so the resume budget must be far larger than the send timeout
    # of 30s -- which missed both by a hair and made the resume frame look like
    # it did not exist at all.
    "x5_poll": BleTransportProfile(
        standard_chunk_cap=512,
        prefer_generic_notify=True,
        flow_controlled_standard_write=True,
        flow_resume_timeout_s=600.0,
    ),
}


def get_ble_transport_profile(
    protocol_family: ProtocolFamily | str | None,
    control_algorithm: str | None = None,
) -> BleTransportProfile:
    if control_algorithm:
        override = _CONTROL_ALGORITHM_PROFILES.get(control_algorithm)
        if override is not None:
            return override
    family = ProtocolFamily.from_value(protocol_family)
    return _PROFILES.get(family, _FALLBACK_PROFILE)
