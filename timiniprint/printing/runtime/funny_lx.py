from __future__ import annotations

import secrets
from collections.abc import Callable

from ...devices.profiles import DetectionNormalizer
from ...protocol.families.funny_lx.core import challenge_crc
from .base import RuntimeController, RuntimeSessionApi

_HANDSHAKE_RANDOM_BYTES = 10


class FunnyLxRuntimeController(RuntimeController):
    def __init__(
        self,
        *,
        bluetooth_address: str,
        random_bytes_factory: Callable[[], bytes] | None = None,
    ) -> None:
        self._bluetooth_address = bluetooth_address
        self._random_bytes_factory = random_bytes_factory or _random_challenge
        self._verified = False

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if isinstance(previous, FunnyLxRuntimeController):
            self._verified = previous._verified

    async def initialize_connection(
        self,
        session: RuntimeSessionApi,
        *,
        mtu_size: int,
        timeout: float,
    ) -> None:
        if self._verified:
            return
        if not session.can_send_control_packet_wait_notification():
            raise RuntimeError("Funny LX verification requires BLE notification queries")

        status = await session.send_control_packet_wait_notification(
            b"\x5A\x01\x00",
            label="Funny LX status",
            match=lambda reply: reply.startswith(b"\x5A\x01"),
            timeout=timeout,
        )
        mac_bytes = _mac_bytes_from_status(status) if status is not None else None
        mac_source = "status"
        if mac_bytes is None:
            mac_bytes = _mac_bytes_from_address(self._bluetooth_address)
            mac_source = "address"
        if mac_bytes is None:
            raise RuntimeError("Funny LX verification could not resolve printer MAC address")

        random_bytes = self._random_bytes_factory()
        if len(random_bytes) != _HANDSHAKE_RANDOM_BYTES:
            raise ValueError(
                f"Funny LX challenge must contain {_HANDSHAKE_RANDOM_BYTES} random bytes"
            )
        crc = challenge_crc(random_bytes, mac_bytes)

        await session.send_control_packet_wait_notification(
            b"\x5A\x0A" + random_bytes,
            label="Funny LX challenge low CRC",
            match=lambda reply: reply.startswith(b"\x5A\x0A") and reply[2 : 2 + len(crc.low)] == crc.low,
            timeout=timeout,
        )
        await session.send_control_packet_wait_notification(
            b"\x5A\x0B" + crc.high,
            label="Funny LX challenge high CRC",
            match=lambda reply: reply.startswith(b"\x5A\x0B\x01"),
            timeout=timeout,
        )
        self._verified = True
        session.report_debug(
            "Funny LX verification complete "
            f"mtu_payload={mtu_size} mac={mac_bytes.hex(':')} mac_source={mac_source}"
        )

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "verified": self._verified,
            "bluetooth_address": self._bluetooth_address,
        }


def _random_challenge() -> bytes:
    return bytes(secrets.randbelow(0xFE) + 1 for _ in range(_HANDSHAKE_RANDOM_BYTES))


def _mac_bytes_from_address(address: str) -> bytes | None:
    if not DetectionNormalizer.is_mac_like_address(address):
        return None
    return bytes.fromhex(DetectionNormalizer.normalize_mac_candidate(address))


def _mac_bytes_from_status(status: bytes) -> bytes | None:
    if len(status) < 10:
        return None
    return bytes(status[4:10])
