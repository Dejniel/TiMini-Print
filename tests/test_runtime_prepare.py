from __future__ import annotations

import asyncio
from dataclasses import replace
import unittest
from unittest.mock import patch

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.device import SerialTarget
from timiniprint.printing.runtime.base import RuntimeController
from timiniprint.printing.runtime.prepare import prepare_connection_runtime
from timiniprint.protocol import ProtocolFamily


class _Connection:
    async def attach_runtime_controller(
        self,
        _runtime_controller,
        *,
        timeout: float = 1.0,
    ) -> None:
        _ = timeout


class _ResolvingController(RuntimeController):
    def __init__(self, resolved_device) -> None:
        self._resolved_device = resolved_device

    def resolve_device(self, _device):
        return self._resolved_device


class RuntimePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = PrinterCatalog.load().device_from_profile("x6h")

    def test_runtime_resolution_can_change_print_profile_fields(self) -> None:
        profile = replace(
            self.device.profile,
            size=self.device.profile.size + 1,
            paper_presets=(
                replace(
                    self.device.profile.default_paper_preset,
                    paper_width_px=392,
                    render_width_px=392,
                ),
            ),
        )
        resolved = replace(self.device, profile=profile)

        with patch(
            "timiniprint.printing.runtime.prepare.runtime_controller_for_device",
            return_value=_ResolvingController(resolved),
        ):
            context = asyncio.run(
                prepare_connection_runtime(self.device, _Connection())
            )

        self.assertIs(context.resolved_device, resolved)

    def test_runtime_resolution_rejects_every_transport_bound_change(self) -> None:
        changed_devices = {
            "protocol_family": replace(
                self.device,
                protocol_family=ProtocolFamily.V5G,
            ),
            "transport_target": replace(
                self.device,
                transport_target=SerialTarget("/dev/test-runtime"),
            ),
            "profile.use_spp": replace(
                self.device,
                profile=replace(
                    self.device.profile,
                    use_spp=not self.device.profile.use_spp,
                ),
            ),
            "profile.stream": replace(
                self.device,
                profile=replace(
                    self.device.profile,
                    stream=replace(
                        self.device.profile.stream,
                        chunk_size=self.device.profile.stream.chunk_size + 1,
                    ),
                ),
            ),
            "profile.ble_mtu_request": replace(
                self.device,
                profile=replace(
                    self.device.profile,
                    ble_mtu_request=self.device.profile.ble_mtu_request + 1,
                ),
            ),
        }

        for field_name, resolved in changed_devices.items():
            with self.subTest(field_name=field_name), patch(
                "timiniprint.printing.runtime.prepare.runtime_controller_for_device",
                return_value=_ResolvingController(resolved),
            ):
                with self.assertRaisesRegex(RuntimeError, field_name.replace(".", r"\.")):
                    asyncio.run(
                        prepare_connection_runtime(self.device, _Connection())
                    )


if __name__ == "__main__":
    unittest.main()
