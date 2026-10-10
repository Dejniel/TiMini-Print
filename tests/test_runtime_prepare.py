from __future__ import annotations

import asyncio
from dataclasses import replace
import unittest
from unittest.mock import patch

from timiniprint.devices import PrinterCatalog
from timiniprint.devices.device import SerialTarget
from timiniprint.printing.runtime.base import PreparedPrinter, RuntimeController
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
        self.activated = False

    async def prepare(self, _device, session, *, timeout):
        return PreparedPrinter(self._resolved_device, self)

    async def after_prepare(self, session, *, timeout):
        self.activated = True


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

        self.assertIs(context.device, resolved)

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
            controller = _ResolvingController(resolved)
            with self.subTest(field_name=field_name), patch(
                "timiniprint.printing.runtime.prepare.runtime_controller_for_device",
                return_value=controller,
            ):
                with self.assertRaises(RuntimeError):
                    asyncio.run(
                        prepare_connection_runtime(self.device, _Connection())
                    )
            self.assertFalse(controller.activated)

    def test_only_final_attached_controller_is_activated(self) -> None:
        for selection in ("retained", "replacement", "stateless"):
            with self.subTest(selection=selection):
                events = []

                class Connection:
                    attached = None

                    async def attach_runtime_controller(self, controller, *, timeout):
                        self.attached = controller
                        events.append(("attach", controller))

                class Controller(RuntimeController):
                    async def after_prepare(self, session, *, timeout):
                        assert connection.attached is self
                        events.append(("activate", self))

                class Bootstrap(Controller):
                    async def prepare(self, device, session, *, timeout):
                        events.append(("prepare", self))
                        return PreparedPrinter(device, selected)

                bootstrap = Bootstrap()
                selected = bootstrap if selection == "retained" else Controller() if selection == "replacement" else None
                connection = Connection()
                asyncio.run(prepare_connection_runtime(self.device, connection, controller=bootstrap))
                self.assertEqual([controller for event, controller in events if event == "activate"],
                                 [selected] if selected else [])
                self.assertEqual(events[:2], [("attach", bootstrap), ("prepare", bootstrap)])
                if selected is not bootstrap:
                    self.assertEqual(events[2], ("attach", selected))

    def test_activation_cleanup_failure_preserves_activation_error(self) -> None:
        class FailingController(RuntimeController):
            async def after_prepare(self, session, *, timeout):
                raise ValueError("activation error")

            async def before_disconnect(self, session):
                raise RuntimeError("cleanup error")

        with self.assertRaisesRegex(ValueError, "activation error"):
            asyncio.run(prepare_connection_runtime(self.device, _Connection(), controller=FailingController()))


if __name__ == "__main__":
    unittest.main()
