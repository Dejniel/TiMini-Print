from __future__ import annotations

from .base import RuntimeController, RuntimeSessionApi
from .phomemo_status import wait_for_phomemo_completion


class PhomemoEscRuntimeController(RuntimeController):
    async def wait_for_completion(
        self,
        session: RuntimeSessionApi,
        *,
        timeout: float,
    ) -> None:
        await wait_for_phomemo_completion(
            session,
            timeout=timeout,
            device_label="Print Master",
        )


__all__ = ["PhomemoEscRuntimeController"]
