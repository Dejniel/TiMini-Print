from __future__ import annotations

from dataclasses import dataclass

from .... import reporting


@dataclass(frozen=True)
class BleWriteCounters:
    notifications: int
    flow_pauses: int
    flow_resumes: int


class BleWriteProgress:
    def __init__(self, label: str, total_chunks: int | None) -> None:
        self._label = label
        self._total_chunks = total_chunks
        self._marks = self._progress_marks(total_chunks)

    def message_for(self, chunk_index: int, byte_count: int, total_bytes: int) -> str | None:
        if self._total_chunks is None or chunk_index not in self._marks:
            return None
        return (
            f"{self._label} progress: chunks={chunk_index}/{self._total_chunks} "
            f"bytes={min(byte_count, total_bytes)}/{total_bytes}"
        )

    @staticmethod
    def _progress_marks(total_chunks: int | None) -> set[int]:
        if total_chunks is None or total_chunks < 200:
            return set()
        return {
            max(1, total_chunks // 4),
            max(1, total_chunks // 2),
            max(1, (total_chunks * 3) // 4),
        }


def report_ble_write_summary(
    reporter: reporting.Reporter,
    label: str,
    *,
    byte_count: int,
    chunks_written: int,
    elapsed_seconds: float,
    before: BleWriteCounters,
    after: BleWriteCounters,
) -> None:
    if not isinstance(chunks_written, int):
        chunks_written = 0
    elapsed_ms = max(0.0, elapsed_seconds * 1000.0)
    avg_chunk_ms = elapsed_ms / chunks_written if chunks_written else 0.0
    bytes_per_second = byte_count / elapsed_seconds if elapsed_seconds > 0 else 0.0
    reporter.debug(
        short="BLE",
        detail=reporting.format_kv(
            label,
            bytes=byte_count,
            chunks=chunks_written,
            elapsed_ms=elapsed_ms,
            avg_chunk_ms=avg_chunk_ms,
            bytes_per_sec=f"{bytes_per_second:.1f}",
            notify_count=after.notifications - before.notifications,
            flow_pause=after.flow_pauses - before.flow_pauses,
            flow_resume=after.flow_resumes - before.flow_resumes,
        ),
    )


def report_ble_write_plan(
    reporter: reporting.Reporter,
    *,
    response: bool,
    strategy: str,
    char_uuid: str,
    payload_bytes: int,
    mtu_payload: int,
    chunk_size: int,
    chunk_count: int,
    reserve: int,
    delay_ms: int,
    payload_head: bytes,
    payload_tail: bytes,
) -> None:
    reporter.debug(
        short="BLE",
        detail=reporting.format_kv(
            "write mode",
            response=response,
            strategy=strategy,
            char=char_uuid,
            payload=payload_bytes,
            mtu_payload=mtu_payload,
            chunk=chunk_size,
            chunks=chunk_count,
            reserve=reserve,
            delay_ms=delay_ms,
            head=payload_head.hex(),
            tail=payload_tail.hex(),
        ),
    )


def report_ble_split_bulk_plan(
    reporter: reporting.Reporter,
    *,
    response: bool,
    payload_bytes: int,
    mtu_payload: int,
    chunk_size: int,
    chunk_count: int,
    reserve: int,
    delay_ms: int,
    flow_control: bool,
) -> None:
    reporter.debug(
        short="BLE",
        detail=reporting.format_kv(
            "split bulk",
            response=response,
            payload=payload_bytes,
            mtu_payload=mtu_payload,
            chunk=chunk_size,
            chunks=chunk_count,
            reserve=reserve,
            delay_ms=delay_ms,
            flow_control=flow_control,
        ),
    )
