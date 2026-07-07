from __future__ import annotations

import asyncio
import secrets
from collections import deque
from collections.abc import Awaitable, Callable

from ...devices.profiles import DetectionNormalizer
from ...protocol.families.funny_lx.core import challenge_crc
from ...protocol.steps import (
    ProtocolReplyExpectation,
    ProtocolStep,
    ProtocolStepOperation,
    reply_matches_expectation,
)
from .base import RuntimeController, RuntimeSessionApi

_HANDSHAKE_RANDOM_BYTES = 10
_MIN_HANDSHAKE_TIMEOUT_SEC = 5.0
_MAX_RETRY_REQUESTS = 10
_MAX_PACKET_DELAY_HINT_SEC = 0.5
_DEFAULT_PACKET_DELAY_HINT_SEC = 0.02
_DEFAULT_DARKNESS_CODE = 3


class FunnyLxRuntimeController(RuntimeController):
    def __init__(
        self,
        *,
        bluetooth_address: str,
        random_bytes_factory: Callable[[], bytes] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._bluetooth_address = bluetooth_address
        self._random_bytes_factory = random_bytes_factory or _random_challenge
        self._sleep = sleep or asyncio.sleep
        self._verified = False
        self._retry_requests: deque[int] = deque()
        self._packet_delay_hint_sec = _DEFAULT_PACKET_DELAY_HINT_SEC
        self._supports_darkness = False
        self._darkness_code: int | None = None

    def adopt_previous(self, previous: RuntimeController | None) -> None:
        if isinstance(previous, FunnyLxRuntimeController):
            self._verified = previous._verified
            self._packet_delay_hint_sec = previous._packet_delay_hint_sec
            self._supports_darkness = previous._supports_darkness
            self._darkness_code = previous._darkness_code

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

        handshake_timeout = _handshake_timeout(timeout)
        status = await session.send_control_packet_wait_notification(
            b"\x5A\x01\x00",
            label="Funny LX status",
            match=lambda reply: reply.startswith(b"\x5A\x01"),
            timeout=handshake_timeout,
        )
        self._supports_darkness = _status_supports_darkness(status or b"")
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
            timeout=handshake_timeout,
        )
        await session.send_control_packet_wait_notification(
            b"\x5A\x0B" + crc.high,
            label="Funny LX challenge high CRC",
            match=lambda reply: reply.startswith(b"\x5A\x0B\x01"),
            timeout=handshake_timeout,
        )
        self._verified = True
        if self._supports_darkness:
            await self._send_default_darkness(session, timeout=timeout)
        session.report_debug(
            "Funny LX verification complete "
            f"mtu_payload={mtu_size} mac={mac_bytes.hex(':')} mac_source={mac_source}"
        )

    async def _send_default_darkness(
        self,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> None:
        if self._darkness_code == _DEFAULT_DARKNESS_CODE:
            return
        if not session.can_send_control_packet():
            session.report_debug("Funny LX default darkness skipped: control send unavailable")
            return
        sent = await session.send_control_packet(
            b"\x5A\x0C" + bytes([_DEFAULT_DARKNESS_CODE]),
            timeout=timeout,
        )
        if sent:
            self._darkness_code = _DEFAULT_DARKNESS_CODE
            session.report_debug("Funny LX default darkness set: level=4")

    async def send_protocol_steps(
        self,
        session: RuntimeSessionApi,
        steps: tuple[ProtocolStep, ...],
        *,
        timeout: float,
    ) -> bool:
        if not any(_is_image_packet(step) for step in steps):
            return False
        if not session.can_send_standard_payload() or not session.can_wait_for_notification():
            return False
        if any(
            step.operation is ProtocolStepOperation.QUERY for step in steps
        ) and not (
            session.can_query_control_packet()
            or session.can_send_control_packet_wait_notification()
        ):
            return False

        self._retry_requests.clear()
        index = 0
        while index < len(steps):
            step = steps[index]
            if _is_image_packet(step):
                image_steps = _image_step_run(steps, index)
                accepted_step = _next_step(steps, index + len(image_steps))
                consume_accepted_step = (
                    accepted_step is not None
                    and accepted_step.operation is ProtocolStepOperation.WAIT
                )
                transfer_ready = await self._send_image_steps_with_retry(
                    session,
                    image_steps,
                    accepted_step=accepted_step if consume_accepted_step else None,
                    timeout=timeout,
                )
                if consume_accepted_step and not transfer_ready:
                    raise RuntimeError(
                        "Funny LX image transfer did not reach printer-ready state "
                        "before the footer step"
                    )
                index += len(image_steps) + (1 if consume_accepted_step else 0)
                continue
            await self._execute_non_image_step(session, step, timeout=timeout)
            index += 1
        return True

    async def _execute_non_image_step(
        self,
        session: RuntimeSessionApi,
        step: ProtocolStep,
        *,
        timeout: float,
    ) -> None:
        darkness_code = _darkness_code_from_step(step)
        if darkness_code is None:
            await _execute_step(session, step, timeout=timeout)
            return
        if self._darkness_code == darkness_code:
            session.report_debug(f"Funny LX darkness already set: level={darkness_code + 1}")
            return
        await _execute_step(session, step, timeout=timeout)
        self._darkness_code = darkness_code

    async def _send_image_steps_with_retry(
        self,
        session: RuntimeSessionApi,
        image_steps: tuple[ProtocolStep, ...],
        *,
        accepted_step: ProtocolStep | None,
        timeout: float,
    ) -> bool:
        image_index = 0
        retry_count = 0
        while True:
            while image_index < len(image_steps) or self._retry_requests:
                retry_index = self._pop_retry_request()
                if retry_index is not None:
                    retry_count, image_index = _apply_retry_request(
                        session,
                        retry_index=retry_index,
                        retry_count=retry_count,
                        packet_count=len(image_steps),
                        current_index=image_index,
                    )
                    continue

                if image_index >= len(image_steps):
                    break
                step = image_steps[image_index]
                packet_index = _image_packet_index(step.data)
                await self._sleep_before_image_packet(session)
                session.report_debug(f"Funny LX image send {step.label}: packet={packet_index}")
                await session.send_standard_payload(step.data)
                image_index += 1

            if accepted_step is None:
                return True
            reply = await _wait_for_image_transfer_ready_or_retry(
                session,
                accepted_step,
                timeout=timeout,
            )
            retry_index = _retry_index_from_notification(reply or b"")
            if retry_index is None:
                transfer_ready = _reply_matches_for(accepted_step, reply)
                if not transfer_ready:
                    session.report_warning(
                        short=f"Funny LX {accepted_step.label} reply mismatch",
                        detail=(
                            f"Expected protocol reply for {accepted_step.label!r}, "
                            f"got {_hex_preview(reply)}."
                        ),
                    )
                return transfer_ready
            self._remove_retry_request(retry_index)
            retry_count, image_index = _apply_retry_request(
                session,
                retry_index=retry_index,
                retry_count=retry_count,
                packet_count=len(image_steps),
                current_index=image_index,
            )

    async def _sleep_before_image_packet(self, session: RuntimeSessionApi) -> None:
        if self._packet_delay_hint_sec <= 0:
            return
        session.report_debug(f"Funny LX packet delay: {self._packet_delay_hint_sec:.3f}s")
        await self._sleep(self._packet_delay_hint_sec)

    def _pop_retry_request(self) -> int | None:
        if not self._retry_requests:
            return None
        return self._retry_requests.popleft()

    def _remove_retry_request(self, retry_index: int) -> None:
        try:
            self._retry_requests.remove(retry_index)
        except ValueError:
            return

    def handle_notification(self, session: RuntimeSessionApi, payload: bytes) -> None:
        retry_index = _retry_index_from_notification(payload)
        if retry_index is None:
            delay_hint = _delay_hint_from_notification(payload)
            if delay_hint is not None:
                self._set_packet_delay_hint(session, delay_hint)
                return
            if _is_pause_notification(payload):
                session.report_debug(f"Funny LX pause notification: {payload.hex(' ')}")
            return
        self._retry_requests.append(retry_index)
        session.report_debug(f"Funny LX retry requested packet={retry_index}")

    def _set_packet_delay_hint(self, session: RuntimeSessionApi, delay_sec: float) -> None:
        clamped = max(0.0, min(delay_sec, _MAX_PACKET_DELAY_HINT_SEC))
        if clamped != self._packet_delay_hint_sec:
            session.report_debug(f"Funny LX packet delay hint: {clamped:.3f}s")
        self._packet_delay_hint_sec = clamped

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "verified": self._verified,
            "bluetooth_address": self._bluetooth_address,
            "packet_delay_hint_sec": self._packet_delay_hint_sec,
            "supports_darkness": self._supports_darkness,
            "darkness_code": self._darkness_code,
        }


def _random_challenge() -> bytes:
    return bytes(secrets.randbelow(0xFE) + 1 for _ in range(_HANDSHAKE_RANDOM_BYTES))


def _handshake_timeout(timeout: float) -> float:
    return max(timeout, _MIN_HANDSHAKE_TIMEOUT_SEC)


def _mac_bytes_from_address(address: str) -> bytes | None:
    if not DetectionNormalizer.is_mac_like_address(address):
        return None
    return bytes.fromhex(DetectionNormalizer.normalize_mac_candidate(address))


def _mac_bytes_from_status(status: bytes) -> bytes | None:
    if len(status) < 10:
        return None
    return bytes(status[4:10])


def _status_supports_darkness(status: bytes) -> bool:
    return len(status) >= 4 and status[2:4] == b"\x00\x03"


def _is_image_packet(step: ProtocolStep) -> bool:
    return (
        step.operation is ProtocolStepOperation.SEND
        and len(step.data) >= 3
        and step.data[0] == 0x55
    )


def _darkness_code_from_step(step: ProtocolStep) -> int | None:
    if (
        step.operation is ProtocolStepOperation.SEND
        and len(step.data) == 3
        and step.data[:2] == b"\x5A\x0C"
    ):
        return step.data[2]
    return None


def _image_step_run(steps: tuple[ProtocolStep, ...], start: int) -> tuple[ProtocolStep, ...]:
    end = start
    while end < len(steps) and _is_image_packet(steps[end]):
        end += 1
    return steps[start:end]


def _next_step(steps: tuple[ProtocolStep, ...], index: int) -> ProtocolStep | None:
    if index >= len(steps):
        return None
    return steps[index]


def _image_packet_index(packet: bytes) -> int:
    return int.from_bytes(packet[1:3], "big")


def _retry_index_from_notification(payload: bytes) -> int | None:
    if len(payload) < 4 or payload[:2] != b"\x5A\x05":
        return None
    return int.from_bytes(payload[2:4], "big")


def _delay_hint_from_notification(payload: bytes) -> float | None:
    if len(payload) < 3 or payload[:2] != b"\x5A\x07":
        return None
    return payload[2] / 1000.0


def _is_pause_notification(payload: bytes) -> bool:
    return payload.startswith(b"\x5A\x08")


def _resume_image_index(requested_index: int, packet_count: int) -> int:
    if packet_count <= 0:
        return 0
    return max(0, min(requested_index - 1, packet_count - 1))


def _apply_retry_request(
    session: RuntimeSessionApi,
    *,
    retry_index: int,
    retry_count: int,
    packet_count: int,
    current_index: int,
) -> tuple[int, int]:
    if retry_count >= _MAX_RETRY_REQUESTS:
        session.report_warning(
            short="Funny LX retry limit exceeded",
            detail="Printer kept requesting image packet resend; continuing without more rewinds.",
        )
        return retry_count, current_index
    retry_count += 1
    image_index = _resume_image_index(retry_index, packet_count)
    session.report_debug(f"Funny LX retry request packet={retry_index} resume={image_index}")
    return retry_count, image_index


async def _execute_step(
    session: RuntimeSessionApi,
    step: ProtocolStep,
    *,
    timeout: float,
) -> None:
    # TODO: This deliberately duplicates part of printing.send's generic step
    # executor so Funny LX can keep `5A 05` resend policy in runtime, not
    # transport. If another runtime needs custom step execution, extract a small
    # shared executor that accepts family hooks instead of growing this copy.
    if step.operation is ProtocolStepOperation.SEND:
        session.report_debug(f"Funny LX protocol send {step.label}: {step.data.hex(' ')}")
        await session.send_standard_payload(step.data)
        return
    if step.operation is ProtocolStepOperation.WAIT:
        reply = await _wait_step(session, step, timeout=timeout)
    elif step.operation is ProtocolStepOperation.QUERY:
        reply = await _query_step(session, step, timeout=timeout)
    else:
        raise ValueError(f"Unsupported Funny LX protocol step operation: {step.operation.value}")
    if not _reply_matches_for(step, reply):
        session.report_warning(
            short=f"Funny LX {step.label} reply mismatch",
            detail=f"Expected protocol reply for {step.label!r}, got {_hex_preview(reply)}.",
        )


async def _wait_step(
    session: RuntimeSessionApi,
    step: ProtocolStep,
    *,
    timeout: float,
) -> bytes | None:
    reply_complete = _reply_complete_for(step)
    if reply_complete is None:
        return None
    wait_timeout = timeout if step.timeout_sec is None else step.timeout_sec
    reply = await session.wait_for_notification(
        step.label,
        reply_complete,
        timeout=wait_timeout,
        required=False,
    )
    session.report_debug(f"Funny LX wait {step.label}: rx={_hex_preview(reply)}")
    return reply


async def _wait_for_image_transfer_ready_or_retry(
    session: RuntimeSessionApi,
    step: ProtocolStep,
    *,
    timeout: float,
) -> bytes | None:
    reply_complete = _reply_complete_for(step)
    if reply_complete is None:
        return None

    def complete(reply: bytes) -> bool:
        return (
            _retry_index_from_notification(reply) is not None
            or reply_complete(reply)
        )

    wait_timeout = timeout if step.timeout_sec is None else step.timeout_sec
    reply = await session.wait_for_notification(
        step.label,
        complete,
        timeout=wait_timeout,
        required=False,
    )
    session.report_debug(f"Funny LX wait {step.label}: rx={_hex_preview(reply)}")
    return reply


async def _query_step(
    session: RuntimeSessionApi,
    step: ProtocolStep,
    *,
    timeout: float,
) -> bytes | None:
    query_timeout = timeout if step.timeout_sec is None else step.timeout_sec
    reply_complete = _reply_complete_for(step)
    if session.can_query_control_packet():
        reply = await session.query_control_packet(
            step.data,
            timeout=query_timeout,
            reply_complete=reply_complete,
        )
    elif reply_complete is not None:
        reply = await session.send_control_packet_wait_notification(
            step.data,
            label=step.label,
            match=reply_complete,
            timeout=query_timeout,
            required=False,
        )
    else:
        await session.send_standard_payload(step.data)
        reply = None
    session.report_debug(
        f"Funny LX query {step.label}: tx={step.data.hex(' ')} rx={_hex_preview(reply)}"
    )
    return reply


def _reply_complete_for(step: ProtocolStep):
    if step.reply_matcher is not None:
        return step.reply_matcher.complete
    if step.expect is ProtocolReplyExpectation.NONE:
        return None
    return lambda reply: reply_matches_expectation(step.expect, reply)


def _reply_matches_for(step: ProtocolStep, reply: bytes | None) -> bool:
    if step.reply_matcher is not None:
        if step.reply_matcher.matches is not None:
            return step.reply_matcher.matches(reply)
        return bool(reply and step.reply_matcher.complete(reply))
    return reply_matches_expectation(step.expect, reply)


def _hex_preview(data: bytes | None) -> str:
    if data is None:
        return "<none>"
    if not data:
        return "<empty>"
    if len(data) <= 32:
        return data.hex(" ")
    return f"{data[:16].hex(' ')} ... {data[-16:].hex(' ')} ({len(data)} bytes)"
