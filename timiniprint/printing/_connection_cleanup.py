"""Connection release that survives repeated caller cancellation."""
import asyncio

from ..transport.base import ConnectionCleanupError, ControlConnection, PrinterConnection


async def disconnect_with_deadline(connection: PrinterConnection | ControlConnection, timeout: float | None = None) -> None:
    task = asyncio.create_task(connection.disconnect())
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    cancelled = None
    cleanup_error = None
    while True:
        try:
            done, _ = await asyncio.wait({task}, timeout=None if deadline is None else max(0, deadline - loop.time()))
        except asyncio.CancelledError as error:
            cancelled = error
            continue
        if not done:
            task.cancel()
            task.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
            cleanup_error = TimeoutError("Printer connection cleanup exceeded its deadline")
            break
        try:
            task.result()
        except asyncio.CancelledError as error:
            cleanup_error = RuntimeError("Printer connection cleanup was cancelled")
            cleanup_error.__cause__ = error
        except Exception as error:
            cleanup_error = error
        break
    if cancelled is not None:
        if cleanup_error is not None:
            raise cancelled from ConnectionCleanupError(cleanup_error)
        raise cancelled
    if cleanup_error is not None:
        raise cleanup_error
