from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from timiniprint import reporting
from timiniprint.app.gui import ManualBluetoothSelection, TiMiniPrintGUI
from timiniprint.devices import PrinterCatalog
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.families import get_protocol_definition
from timiniprint.transport.bluetooth import BluetoothDiscovery, BluetoothScanResult
from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport
from timiniprint.update_check import UpdateCheckResult


class GuiPaperMotionStatusTests(unittest.TestCase):
    def test_restore_status_after_paper_motion_uses_connected_when_connected(self) -> None:
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        seen: list[str] = []
        gui.connected_device = object()
        gui._queue_status = lambda key, **ctx: seen.append(key)

        gui._restore_status_after_paper_motion()

        self.assertEqual(seen, [reporting.STATUS_CONNECT_DONE])

    def test_restore_status_after_paper_motion_uses_idle_when_disconnected(self) -> None:
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        seen: list[str] = []
        gui.connected_device = None
        gui._queue_status = lambda key, **ctx: seen.append(key)

        gui._restore_status_after_paper_motion()

        self.assertEqual(seen, [reporting.STATUS_IDLE])


class GuiPaperModeChoiceTests(unittest.TestCase):
    def test_paper_mode_choices_are_empty_for_unsupported_family(self) -> None:
        catalog = PrinterCatalog.load()
        device = catalog.device_from_profile("x6h")

        self.assertEqual(TiMiniPrintGUI._paper_mode_choices_for_device(device), ())

    def test_paper_mode_choices_are_exposed_for_luck_normal_a4(self) -> None:
        catalog = PrinterCatalog.load()
        base = catalog.device_from_profile("x6h")
        device = base.__class__(
            display_name=base.display_name,
            profile=base.profile,
            protocol_family=ProtocolFamily.LUCK_NORMAL_A4,
            protocol_variant=base.protocol_variant,
            image_pipeline=get_protocol_definition(ProtocolFamily.LUCK_NORMAL_A4).behavior.default_image_pipeline,
            runtime_settings=base.runtime_settings,
            transport_target=base.transport_target,
            model_key=base.model_key,
        )

        labels = [label for label, _mode in TiMiniPrintGUI._paper_mode_choices_for_device(device)]
        self.assertEqual(labels, ["Plain roll", "Tag", "Black tag", "Folder", "Tattoo"])

    def test_paper_mode_choices_follow_qirui_variant_subset(self) -> None:
        catalog = PrinterCatalog.load()
        device = catalog.detect_device("QIRUI_Q2_1234")

        self.assertIsNotNone(device)
        labels = [label for label, _mode in TiMiniPrintGUI._paper_mode_choices_for_device(device)]
        self.assertEqual(labels, ["Plain roll", "Tag"])

    def test_default_paper_mode_label_uses_profile_default(self) -> None:
        catalog = PrinterCatalog.load()
        device = catalog.device_from_profile("luck_ppa2l")

        self.assertEqual(TiMiniPrintGUI._default_paper_mode_label_for_device(device), "Tag")


class GuiDeviceListTests(unittest.TestCase):
    def test_scan_display_devices_include_ambiguous_supported_source_variants(self) -> None:
        catalog = PrinterCatalog.load()
        endpoint = DeviceInfo(
            name="P1",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )
        result = BluetoothScanResult(
            devices=[],
            failures=[],
            raw_endpoints=[endpoint],
        )
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        gui.catalog = catalog
        gui.discovery = BluetoothDiscovery(catalog)

        devices = gui._scan_devices_for_display(result)
        labels = [gui._device_label(device) for device in devices]

        self.assertEqual(
            {device.model_key for device in devices},
            {"pocket_printer", "toprint_tspl_p1"},
        )
        self.assertTrue(any("Tiny Print: pocket_printer" in label for label in labels))
        self.assertTrue(any("ToPrint: toprint_tspl_p1" in label for label in labels))

    def test_scan_status_count_uses_supported_devices_only(self) -> None:
        supported = DeviceInfo(
            name="X6H-ABCD",
            address="AA:BB:CC:DD:EE:01",
            transport=DeviceTransport.CLASSIC,
        )
        unsupported = DeviceInfo(
            name="MysteryPrinter",
            address="AA:BB:CC:DD:EE:02",
            transport=DeviceTransport.BLE,
        )
        catalog = PrinterCatalog.load()
        result = BluetoothScanResult(
            devices=[catalog.detect_device(supported.name, supported.address)],
            failures=[],
            raw_endpoints=[supported, unsupported],
        )
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)

        self.assertEqual(gui._scan_result_status_count(result), 1)

    def test_unknown_devices_are_displayed_only_when_manual_mode_is_enabled(self) -> None:
        unknown = DeviceInfo(
            name="MysteryPrinter",
            address="AA:BB:CC:DD:EE:02",
            transport=DeviceTransport.BLE,
        )
        catalog = PrinterCatalog.load()
        result = BluetoothScanResult(
            devices=[],
            failures=[],
            raw_endpoints=[unknown],
        )
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        gui.catalog = catalog
        gui.discovery = BluetoothDiscovery(catalog)

        self.assertEqual(gui._scan_devices_for_display(result), [])

        gui.show_unknown_devices_var = _FakeVar(True)
        devices = gui._scan_devices_for_display(result)

        self.assertEqual(len(devices), 1)
        self.assertIsInstance(devices[0], ManualBluetoothSelection)
        self.assertIn("manual model required", gui._device_label(devices[0]))

    def test_manual_model_selection_builds_effective_device_from_raw_target(self) -> None:
        catalog = PrinterCatalog.load()
        unknown = DeviceInfo(
            name="MysteryPrinter",
            address="AA:BB:CC:DD:EE:02",
            transport=DeviceTransport.BLE,
        )
        result = BluetoothScanResult(
            devices=[],
            failures=[],
            raw_endpoints=[unknown],
        )
        target = BluetoothDiscovery(catalog).manual_targets_for_display(result)[0]
        selection = ManualBluetoothSelection(display_name=target.display_name, target=target)
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        gui.catalog = catalog
        gui._manual_model_choice_map = gui._build_manual_model_choice_map()
        choice = next(
            label
            for label, model_key in gui._manual_model_choice_map.items()
            if model_key == "gt01"
        )
        label = gui._device_label(selection)
        gui.device_var = _FakeVar(label)
        gui.manual_model_var = _FakeVar(choice)
        gui.device_map = {label: selection}

        device = gui._effective_selected_device()

        self.assertIsNotNone(device)
        assert device is not None
        self.assertEqual(device.model_key, "gt01")
        self.assertEqual(device.display_name, "MysteryPrinter")
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:02")

    def test_manual_model_selection_resets_when_raw_target_changes(self) -> None:
        catalog = PrinterCatalog.load()
        result = BluetoothScanResult(
            devices=[],
            failures=[],
            raw_endpoints=[
                DeviceInfo(
                    name="MysteryOne",
                    address="AA:BB:CC:DD:EE:02",
                    transport=DeviceTransport.BLE,
                ),
                DeviceInfo(
                    name="MysteryTwo",
                    address="AA:BB:CC:DD:EE:03",
                    transport=DeviceTransport.BLE,
                ),
            ],
        )
        targets = BluetoothDiscovery(catalog).manual_targets_for_display(result)
        first = ManualBluetoothSelection(display_name=targets[0].display_name, target=targets[0])
        second = ManualBluetoothSelection(display_name=targets[1].display_name, target=targets[1])
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        gui.device_var = _FakeVar(gui._device_label(first))
        gui.manual_model_var = _FakeVar("some model")
        gui.device_map = {
            gui._device_label(first): first,
            gui._device_label(second): second,
        }
        gui._manual_model_target_key = gui._selected_manual_target_key()

        gui.device_var.set(gui._device_label(second))
        gui._reset_manual_model_if_target_changed()

        self.assertEqual(gui.manual_model_var.get(), "")

    def test_connected_manual_device_keeps_manual_profile_label(self) -> None:
        catalog = PrinterCatalog.load()
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        gui.connected_device = catalog.device_from_model("gt01")
        gui.connected_device_was_manual = True
        gui.profile_var = _FakeVar()

        gui._refresh_profile_label()

        self.assertEqual(gui.profile_var.get(), "GT01 (MANUAL)")

    def test_scan_uses_blocking_discovery_worker(self) -> None:
        catalog = PrinterCatalog.load()
        result = BluetoothScanResult(
            devices=[catalog.detect_device("X6H-ABCD", "AA:BB:CC:DD:EE:01")],
            failures=[],
            raw_endpoints=[],
        )
        queued = []
        statuses = []
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        gui._closing = False
        gui._scan_busy = False
        gui.discovery = SimpleNamespace(
            scan_report_blocking=lambda: result,
            devices_for_display=lambda result: list(result.devices),
        )
        gui.queue = SimpleNamespace(put=queued.append)
        gui._queue_status = lambda key, **ctx: statuses.append((key, ctx))
        gui._queue_warning = lambda *args, **kwargs: None
        gui._queue_error = lambda *args, **kwargs: None

        with patch("timiniprint.app.gui.threading.Thread", _InlineThread):
            gui.scan()

        self.assertFalse(gui._scan_busy)
        self.assertEqual(queued, [("devices", result)])
        self.assertEqual(statuses[0][0], reporting.STATUS_SCAN_START)
        self.assertEqual(statuses[-1], (reporting.STATUS_SCAN_DONE, {"count": 1}))

    def test_scan_failure_clears_busy_state(self) -> None:
        errors = []
        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        gui._closing = False
        gui._scan_busy = False
        gui.discovery = SimpleNamespace(scan_report_blocking=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        gui._queue_status = lambda *args, **kwargs: None
        gui._queue_warning = lambda *args, **kwargs: None
        gui._queue_error = lambda key, **ctx: errors.append((key, ctx))

        with patch("timiniprint.app.gui.threading.Thread", _InlineThread):
            gui.scan()

        self.assertFalse(gui._scan_busy)
        self.assertEqual(errors[0][0], reporting.ERROR_SCAN_FAILED)


class GuiUpdateButtonTests(unittest.TestCase):
    def test_show_update_button_sets_release_url_and_packs_button_before_print(self) -> None:
        class FakeButton:
            def __init__(self) -> None:
                self.text = ""
                self.pack_kwargs = None

            def configure(self, **kwargs) -> None:
                self.text = kwargs["text"]

            def winfo_ismapped(self) -> bool:
                return False

            def pack(self, **kwargs) -> None:
                self.pack_kwargs = kwargs

        gui = TiMiniPrintGUI.__new__(TiMiniPrintGUI)
        gui.update_button = FakeButton()
        gui.print_button = object()

        gui._show_update_button(
            UpdateCheckResult(
                current_version="0.5",
                latest_version="v0.6",
                release_url="https://example.test/releases/v0.6",
            )
        )

        self.assertEqual(gui._update_release_url, "https://example.test/releases/v0.6")
        self.assertEqual(gui.update_button.text, "Update v0.6")
        self.assertEqual(gui.update_button.pack_kwargs["side"], "right")
        self.assertEqual(gui.update_button.pack_kwargs["before"], gui.print_button)


class _InlineThread:
    def __init__(self, *, target, name=None, daemon=None):
        self._target = target
        self.name = name
        self.daemon = daemon

    def start(self) -> None:
        self._target()


class _FakeVar:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


if __name__ == "__main__":
    unittest.main()
