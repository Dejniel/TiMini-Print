"""Bounded Bleak operations with ownership of unfinished cancellation."""
from __future__ import annotations

import asyncio

from ... import reporting

BLE_CLEANUP_TIMEOUT = 5.0


class _BleakLifecycle:
    def __init__(self, reporter: reporting.Reporter = reporting.DUMMY_REPORTER) -> None:
        self._reporter = reporter
        self._tasks: set[asyncio.Task] = set()

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def run(self, operation, *, timeout: float, label: str):
        task = asyncio.ensure_future(operation)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        try:
            # wait_for() can exceed its deadline while awaiting cancellation.
            # Keep ownership instead, so disconnect can finish that cancellation.
            done, _ = await asyncio.wait({task}, timeout=max(0.0, timeout))
            if not done:
                raise TimeoutError(f"Timed out during BLE {label}")
            return task.result()
        except BaseException:
            if not task.done():
                task.cancel()
            raise

    async def disconnect(self, client, *, before_disconnect=None,
                         timeout: float | None = None) -> None:
        """Finish cleanup despite caller cancellation, without claiming success early."""
        timeout = BLE_CLEANUP_TIMEOUT if timeout is None else timeout
        cleanup = asyncio.create_task(self._disconnect(client, before_disconnect, timeout))
        cancelled = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                cancelled = exc
            except Exception:
                break
        if cancelled is not None:
            try:
                cleanup.result()
            except Exception as exc:
                self._reporter.warning(short="BLE cleanup failed", detail=str(exc))
            raise cancelled
        cleanup.result()

    async def _disconnect(self, client, before_disconnect, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        if before_disconnect is not None:
            try:
                # Reserve time for disconnect even if notification shutdown stalls.
                await self.run(before_disconnect, timeout=timeout / 2, label="notification shutdown")
            except (Exception, asyncio.CancelledError) as exc:
                self._reporter.warning(short="BLE notification shutdown failed", detail=str(exc))
        error = None
        try:
            if client is not None:
                await self.run(client.disconnect(), timeout=deadline - loop.time(), label="disconnect")
        except (Exception, asyncio.CancelledError) as exc:
            error = exc
        tasks = {task for task in self._tasks if not task.done()}
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline - loop.time()))
            if pending:
                raise RuntimeError("BLE cleanup incomplete: operations are still pending") from error
        if error is not None:
            raise error
        if client is not None and getattr(client, "is_connected", False):
            raise RuntimeError("BLE cleanup incomplete: client is still connected")
