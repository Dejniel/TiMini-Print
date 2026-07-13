# TODO: DO NOT READ. This code is waiting to be rewritten :P
# One day I’ll refactor the whole GUI properly;
# for now, the terrible single-file monolith stays.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, ttk

from .diagnostics import emit_startup_warnings
from .. import reporting
from ..devices import PrinterCatalog, PrinterDevice, ResolvedBluetoothTarget
from ..licensing import license_text
from ..printing.connected import ConnectedPrinter, connect_printer
from ..printing.paper import default_paper_preset_for_device, paper_presets_for_device
from ..printing.settings import PrintSettings
from ..rendering.converters.text import TextConverter
from ..rendering.formats import normalized_width
from ..transport.bluetooth import BleakBluetoothConnector, BluetoothDiscovery, BluetoothScanResult
from ..transport.bluetooth.types import DeviceTransport
from ..update_check import UpdateCheckResult, check_for_updates, should_check_for_updates

PAPER_MOTION_INTERVAL_MS = 1000


@dataclass(frozen=True)
class ManualBluetoothSelection:
    """Raw Bluetooth target that needs an explicit catalog model selection."""

    display_name: str
    target: ResolvedBluetoothTarget

    @property
    def address(self) -> str:
        return self.target.transport_target.display_address

    @property
    def paired(self) -> bool | None:
        return self.target.transport_target.paired

    @property
    def transport_badge(self) -> str:
        return self.target.transport_target.transport_badge


class BleLoop:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._shutdown_timeout = 2.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._loop_ready.wait(timeout=2.0):
            raise RuntimeError("BLE event loop did not start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._loop_ready.set()
        try:
            loop.run_forever()
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(self._shutdown_default_executor(loop))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    async def _shutdown_default_executor(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            await loop.shutdown_default_executor(timeout=self._shutdown_timeout)
        except TypeError:
            await loop.shutdown_default_executor()

    def submit(self, coro, callback=None):
        self._loop_ready.wait()
        loop = self._loop
        if loop is None or loop.is_closed():
            coro.close()
            raise RuntimeError("BLE event loop is closed")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        if callback:
            future.add_done_callback(callback)
        return future

    def shutdown(self, timeout: float = 2.0) -> None:
        self._loop_ready.wait(timeout)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        self._shutdown_timeout = timeout
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout + 0.2)


class TiMiniPrintGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        emit_startup_warnings()
        self.title("TiMini Print")
        self.resizable(True, True)

        self.catalog = PrinterCatalog.load()
        self.ble_loop = BleLoop()
        self.queue: queue.Queue = queue.Queue()
        self._stderr_sink = reporting.StderrSink()
        self.reporter = reporting.Reporter(
            [
                reporting.QueueStatusSink(self.queue, show_warnings=True),
                self._stderr_sink,
            ]
        )
        self.discovery = BluetoothDiscovery(self.catalog, reporter=self.reporter)
        self.connector = BleakBluetoothConnector(reporter=self.reporter)
        self.connected_printer: ConnectedPrinter | None = None

        self.devices = []
        self.device_map = {}
        self._last_scan_result = BluetoothScanResult(devices=[], failures=[], raw_endpoints=[])

        self.device_var = tk.StringVar()
        self.show_unknown_devices_var = tk.BooleanVar(value=False)
        self.manual_model_var = tk.StringVar()
        self.profile_var = tk.StringVar(value="")
        self.file_var = tk.StringVar()
        self.text_mode_var = tk.BooleanVar(value=False)
        self.rotate_90_var = tk.BooleanVar(value=False)
        self.darkness_var = tk.IntVar(value=3)
        self.paper_var = tk.StringVar(value="")
        self.text_font_var = tk.StringVar()
        self.text_columns_var = tk.IntVar(value=35)
        self.text_wrap_var = tk.BooleanVar(value=True)
        self.trim_margins_var = tk.BooleanVar(value=True)
        self.trim_top_bottom_margins_var = tk.BooleanVar(value=True)
        self.pdf_pages_var = tk.StringVar()
        self.pdf_gap_var = tk.IntVar(value=5)
        self.status_var = tk.StringVar(
            value=reporting.MessageCatalog.resolve("status", reporting.STATUS_IDLE) or "Idle"
        )
        self.connected_device = None
        self.connected_device_was_manual = False
        self._connecting = False
        self._paper_motion_action = None
        self._paper_motion_job = None
        self._paper_motion_busy = False
        self._scan_busy = False
        self._layout_ready = False
        self._closing = False
        self._paper_choice_map: dict[str, str] = {}
        self._manual_model_choice_map = self._build_manual_model_choice_map()
        self._update_release_url: str | None = None
        self._licenses_window: tk.Toplevel | None = None
        self.file_var.trace_add("write", self._on_file_path_change)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self.update_idletasks()
        self.minsize(int(self.winfo_reqwidth()*.9), self.winfo_reqheight())

        self._layout_ready = True
        self._set_connected_state(False)
        self.after(100, self._process_queue)
        self.after(200, self.scan)
        self.after(500, self._check_for_updates)
        
    def _build_ui(self) -> None:
        padding = {"padx": 10, "pady": 6}

        device_frame = ttk.LabelFrame(self, text="Bluetooth")
        device_frame.pack(fill="x", padx=10, pady=10)
        device_frame.columnconfigure(1, weight=1)

        ttk.Label(device_frame, text="Device:").grid(row=0, column=0, sticky="w", **padding)
        self.device_combo = ttk.Combobox(device_frame, textvariable=self.device_var, width=48, state="readonly")
        self.device_combo.grid(row=0, column=1, sticky="ew", **padding)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selection_changed)

        self.refresh_button = ttk.Button(device_frame, text="Refresh", command=self.scan)
        self.refresh_button.grid(row=0, column=2, **padding)
        ttk.Label(device_frame, text="Profile:").grid(row=1, column=0, sticky="w", **padding)
        self.profile_label = ttk.Label(device_frame, textvariable=self.profile_var, width=48)
        self.profile_label.grid(row=1, column=1, sticky="ew", **padding)

        self.connection_button = ttk.Button(device_frame, text="Connect", command=self.toggle_connection)
        self.connection_button.grid(row=1, column=2, sticky="e", **padding)
        self.manual_model_label = ttk.Label(device_frame, text="Treat as model:")
        self.manual_model_label.grid(row=2, column=0, sticky="w", **padding)
        self.manual_model_combo = ttk.Combobox(
            device_frame,
            textvariable=self.manual_model_var,
            values=list(self._manual_model_choice_map.keys()),
            width=48,
            state="readonly",
        )
        self.manual_model_combo.grid(row=2, column=1, sticky="ew", **padding)
        self.manual_model_combo.bind("<<ComboboxSelected>>", self._on_manual_model_selection_changed)
        self.manual_model_label.grid_remove()
        self.manual_model_combo.grid_remove()

        self.file_frame = ttk.LabelFrame(self, text="File")
        self.file_frame.pack(fill="x", padx=10, pady=10)
        self.file_frame.columnconfigure(1, weight=1)

        ttk.Label(self.file_frame, text="Path:").grid(row=0, column=0, sticky="w", **padding)
        self.file_entry = ttk.Entry(self.file_frame, textvariable=self.file_var, width=48)
        self.file_entry.grid(row=0, column=1, sticky="ew", **padding)
        self.browse_button = ttk.Button(self.file_frame, text="Browse", command=self.browse)
        self.browse_button.grid(row=0, column=2, **padding)

        options_frame = ttk.LabelFrame(self, text="Options")
        options_frame.pack(fill="x", padx=10, pady=10)
        checks_frame = ttk.Frame(options_frame)
        checks_frame.grid(row=0, column=0, columnspan=3, sticky="w", **padding)
        self.text_mode_check = ttk.Checkbutton(
            checks_frame,
            text="Firmware text mode",
            variable=self.text_mode_var,
        )
        self.text_mode_check.pack(side="left", padx=(0, 12))
        self.rotate_90_check = ttk.Checkbutton(
            checks_frame,
            text="Rotate 90 deg",
            variable=self.rotate_90_var,
        )
        self.rotate_90_check.pack(side="left", padx=(0, 12))
        self.trim_margins_check = ttk.Checkbutton(
            checks_frame,
            text="Trim side margins",
            variable=self.trim_margins_var,
        )
        self.trim_margins_check.pack(side="left", padx=(0, 12))
        self.trim_top_bottom_margins_check = ttk.Checkbutton(
            checks_frame,
            text="Trim vertical margins",
            variable=self.trim_top_bottom_margins_var,
        )
        self.trim_top_bottom_margins_check.pack(side="left")
        ttk.Label(options_frame, text="Darkness:").grid(row=1, column=0, sticky="w", **padding)
        self.darkness_scale = tk.Scale(
            options_frame,
            from_=1,
            to=5,
            orient="horizontal",
            resolution=1,
            showvalue=False,
            variable=self.darkness_var,
        )
        self.darkness_scale.grid(row=1, column=1, sticky="ew", **padding)
        self.darkness_value_label = ttk.Label(options_frame, textvariable=self.darkness_var, width=2)
        self.darkness_value_label.grid(row=1, column=2, sticky="w", **padding)
        self.paper_label = ttk.Label(options_frame, text="Paper:")
        self.paper_label.grid(row=2, column=0, sticky="w", **padding)
        self.paper_combo = ttk.Combobox(
            options_frame,
            textvariable=self.paper_var,
            width=24,
            state="readonly",
        )
        self.paper_combo.grid(row=2, column=1, sticky="w", **padding)
        self.paper_combo.bind("<<ComboboxSelected>>", self._on_paper_selection_changed)
        options_frame.columnconfigure(1, weight=1)

        self.text_frame = ttk.LabelFrame(self, text="Txt Options")
        self.text_frame.columnconfigure(1, weight=1)
        ttk.Label(self.text_frame, text="Font:").grid(row=0, column=0, sticky="w", **padding)
        self.text_font_entry = ttk.Entry(self.text_frame, textvariable=self.text_font_var, width=48)
        self.text_font_entry.grid(row=0, column=1, sticky="ew", **padding)
        self.text_font_browse = ttk.Button(self.text_frame, text="Browse", command=self.browse_text_font)
        self.text_font_browse.grid(row=0, column=2, **padding)
        self.text_font_clear = ttk.Button(self.text_frame, text="Default", command=self.clear_text_font)
        self.text_font_clear.grid(row=0, column=3, **padding)
        ttk.Label(self.text_frame, text="Letters per line:").grid(row=1, column=0, sticky="w", **padding)
        self.text_columns_scale = tk.Scale(
            self.text_frame,
            from_=30,
            to=40,
            orient="horizontal",
            resolution=1,
            showvalue=False,
            variable=self.text_columns_var,
        )
        self.text_columns_scale.grid(row=1, column=1, sticky="ew", **padding)
        self.text_columns_value_label = ttk.Label(self.text_frame, textvariable=self.text_columns_var, width=4)
        self.text_columns_value_label.grid(row=1, column=2, sticky="w", **padding)
        self.text_wrap_check = ttk.Checkbutton(
            self.text_frame,
            text="Whitespace wrap",
            variable=self.text_wrap_var,
        )
        self.text_wrap_check.grid(row=1, column=3, sticky="w", **padding)

        self.pdf_frame = ttk.LabelFrame(self, text="PDF Options")
        self.pdf_frame.columnconfigure(1, weight=1)
        ttk.Label(self.pdf_frame, text="Pages (e.g. 1-3,5):").grid(row=0, column=0, sticky="w", **padding)
        self.pdf_pages_entry = ttk.Entry(self.pdf_frame, textvariable=self.pdf_pages_var, width=48)
        self.pdf_pages_entry.grid(row=0, column=1, sticky="ew", **padding)
        ttk.Label(self.pdf_frame, text="Page gap (mm):").grid(row=1, column=0, sticky="w", **padding)
        self.pdf_gap_scale = tk.Scale(
            self.pdf_frame,
            from_=0,
            to=50,
            orient="horizontal",
            resolution=1,
            showvalue=False,
            variable=self.pdf_gap_var,
        )
        self.pdf_gap_scale.grid(row=1, column=1, sticky="ew", **padding)
        self.pdf_gap_value_label = ttk.Label(self.pdf_frame, textvariable=self.pdf_gap_var, width=4)
        self.pdf_gap_value_label.grid(row=1, column=2, sticky="w", **padding)

        self.action_frame = ttk.Frame(self)
        self.action_frame.pack(fill="x", padx=10, pady=10)
        self.print_button = ttk.Button(self.action_frame, text="Print", command=self.print_file)
        self.update_button = ttk.Button(self.action_frame, text="Update", command=self.open_update_release)
        self.retract_button = ttk.Button(self.action_frame, text="Retract")
        self.feed_button = ttk.Button(self.action_frame, text="Feed")
        self.feed_button.pack(side="left")
        self.retract_button.pack(side="left", padx=(6, 0))
        self.print_button.pack(side="right")
        self.update_button.pack(side="right", padx=(0, 6))
        self.update_button.pack_forget()
        self.feed_button.bind("<ButtonPress-1>", lambda event: self._start_paper_motion("feed"))
        self.feed_button.bind("<ButtonRelease-1>", self._stop_paper_motion)
        self.feed_button.bind("<Leave>", self._stop_paper_motion)
        self.retract_button.bind("<ButtonPress-1>", lambda event: self._start_paper_motion("retract"))
        self.retract_button.bind("<ButtonRelease-1>", self._stop_paper_motion)
        self.retract_button.bind("<Leave>", self._stop_paper_motion)

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=10, pady=10)
        ttk.Label(status_frame, text="Status:").pack(side="left")
        self.show_unknown_check = ttk.Checkbutton(
            status_frame,
            text="Show unsupported",
            variable=self.show_unknown_devices_var,
            command=self._on_show_unknown_devices_changed,
        )
        self.show_unknown_check.pack(side="right")
        self.licenses_link = ttk.Label(status_frame, text="Licenses", cursor="hand2", takefocus=True)
        self.licenses_link.bind("<Button-1>", self.show_licenses)
        self.licenses_link.bind("<Return>", self.show_licenses)
        self.licenses_link.bind("<space>", self.show_licenses)
        self.licenses_link.pack(side="right", padx=(0, 10))
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left", padx=6, fill="x", expand=True)

        self._update_option_sections(self.file_var.get())
        self._refresh_paper_controls()

    def show_licenses(self, _event=None) -> None:
        if self._licenses_window is not None and self._licenses_window.winfo_exists():
            self._licenses_window.lift()
            self._licenses_window.focus_set()
            return

        window = tk.Toplevel(self)
        self._licenses_window = window
        window.title("TiMini-Print licenses")
        window.geometry("900x650")
        window.minsize(600, 400)

        frame = ttk.Frame(window, padding=8)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        text = tk.Text(frame, wrap="none", font="TkFixedFont", padx=8, pady=8)
        vertical_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        horizontal_scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        text.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        text.insert("1.0", license_text())
        text.configure(state="disabled")

        def close() -> None:
            window.destroy()
            self._licenses_window = None

        window.protocol("WM_DELETE_WINDOW", close)

    def _process_queue(self) -> None:
        if self._closing:
            return
        while True:
            try:
                action, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if action == "status":
                self.status_var.set(payload)
            elif action == "devices":
                self._last_scan_result = payload
                self._refresh_device_list()
            elif action == "connected":
                device = payload
                self._set_connected_state(True, device)
            elif action == "disconnected":
                self._set_connected_state(False)
            elif action == "error":
                self.status_var.set(f"Error: {payload}")
            elif action == "connecting":
                self._set_connecting_state(bool(payload))
            elif action == "update_available":
                self._show_update_button(payload)
        if not self._closing:
            self.after(100, self._process_queue)

    def _check_for_updates(self) -> None:
        if self._closing or not should_check_for_updates(source_builds=True):
            return

        def run_check() -> None:
            try:
                result = check_for_updates()
            except Exception:
                return
            if result is not None and not self._closing:
                self.queue.put(("update_available", result))

        threading.Thread(target=run_check, name="timiniprint-gui-update-check", daemon=True).start()

    def _show_update_button(self, result: UpdateCheckResult) -> None:
        self._update_release_url = result.release_url
        self.update_button.configure(text=f"Update {result.latest_version}")
        if not self.update_button.winfo_ismapped():
            self.update_button.pack(side="right", padx=(0, 6), before=self.print_button)

    def open_update_release(self) -> None:
        if self._update_release_url:
            webbrowser.open(self._update_release_url)

    def _device_label(self, device) -> str:
        if isinstance(device, ManualBluetoothSelection):
            return self._manual_device_label(device)
        name = device.display_name or ""
        transport = f" {device.transport_badge}"
        status = " [unpaired]" if device.paired is False else ""
        source = self._device_source_label(device)
        if name:
            return f"{name}{source} ({device.address}){transport}{status}"
        return f"{device.address}{source}{transport}{status}"

    def _manual_device_label(self, item: ManualBluetoothSelection) -> str:
        name = item.display_name or ""
        transport = f" {item.transport_badge}"
        status = " [unpaired]" if item.paired is False else ""
        source = " [manual model required]"
        if name:
            return f"{name}{source} ({item.address}){transport}{status}"
        return f"{item.address}{source}{transport}{status}"

    def _device_source_label(self, device) -> str:
        app_names = ", ".join(self.catalog.origin_app_names(device.origin_app_packages))
        if not app_names and not device.model_key:
            return ""
        source = app_names or "unknown app"
        model = device.model_key or "unknown model"
        return f" [{source}: {model}]"

    def _show_manual_devices_enabled(self) -> bool:
        var = self.__dict__.get("show_unknown_devices_var")
        return bool(var is not None and var.get())

    def _scan_devices_for_display(self, result: BluetoothScanResult):
        discovery = self.__dict__.get("discovery")
        if discovery is None:
            return list(result.devices)
        devices = list(discovery.devices_for_display(result))
        if not self._show_manual_devices_enabled():
            return devices
        devices.extend(
            ManualBluetoothSelection(
                display_name=target.display_name,
                target=target,
            )
            for target in discovery.manual_targets_for_display(result)
        )
        return devices

    def _refresh_device_list(self) -> None:
        result = self._last_scan_result
        devices = self._scan_devices_for_display(result)
        self.devices = devices
        self.device_map = {self._device_label(device): device for device in devices}

        labels = list(self.device_map.keys())

        self.device_combo["values"] = labels
        current = self.device_var.get()
        if labels:
            if current in labels:
                self.device_var.set(current)
            elif not self.connected_device:
                self.device_var.set(labels[0])
        else:
            self.device_var.set("")
        self._reset_manual_model_if_target_changed()
        self._refresh_manual_model_controls()
        self._refresh_profile_label()
        self._refresh_paper_controls()

    def _queue_status(self, key: str, **ctx) -> None:
        self.reporter.status(key, **ctx)

    def _queue_warning(self, key: str, detail=None, **ctx) -> None:
        self.reporter.warning(key, detail=detail, **ctx)

    def _queue_error(self, key: str, detail=None, exc=None, **ctx) -> None:
        self.reporter.error(key, detail=detail, exc=exc, **ctx)

    def scan(self) -> None:
        if self._closing or self._scan_busy:
            return
        self._scan_busy = True
        self._queue_status(reporting.STATUS_SCAN_START)

        def run_scan() -> None:
            try:
                result = self.discovery.scan_report_blocking()
                if self._closing:
                    return
                self.queue.put(("devices", result))
                for failure in result.failures:
                    if failure.transport == DeviceTransport.BLE:
                        self._queue_warning(reporting.WARNING_SCAN_BLE_FAILED, detail=str(failure.error))
                    else:
                        self._queue_warning(reporting.WARNING_SCAN_CLASSIC_FAILED, detail=str(failure.error))
                self._queue_status(
                    reporting.STATUS_SCAN_DONE,
                    count=self._scan_result_status_count(result),
                )
            except Exception as exc:
                if not self._closing:
                    self._queue_error(reporting.ERROR_SCAN_FAILED, detail=str(exc), exc=exc)
            finally:
                self._scan_busy = False

        threading.Thread(target=run_scan, name="timiniprint-gui-scan", daemon=True).start()

    def _scan_result_status_count(self, result: BluetoothScanResult) -> int:
        return len(self._scan_devices_for_display(result))

    def connect(self) -> None:
        label = self.device_var.get()
        device = self._effective_selected_device()
        if not device:
            self._queue_error(reporting.ERROR_NO_DEVICE)
            return
        self._queue_status(reporting.STATUS_CONNECT_START)
        self.queue.put(("connecting", True))

        def done(fut):
            try:
                self.connected_printer = fut.result()
                self._queue_status(reporting.STATUS_CONNECT_DONE)
                self.queue.put(("connected", device))
            except Exception as exc:
                self._queue_error(reporting.ERROR_CONNECT_FAILED, detail=str(exc), exc=exc)
                self.queue.put(("connecting", False))

        async def run():
            return await connect_printer(device, self.connector, reporter=self.reporter)

        self.ble_loop.submit(run(), callback=done)

    def toggle_connection(self) -> None:
        if self._connecting:
            return
        if self.connected_device:
            self.disconnect()
        else:
            self.connect()

    def disconnect(self) -> None:
        self._queue_status(reporting.STATUS_DISCONNECT_START)
        connected = self.connected_printer

        def done(fut):
            try:
                fut.result()
                self.connected_printer = None
                self._queue_status(reporting.STATUS_DISCONNECT_DONE)
                self.queue.put(("disconnected", None))
            except Exception as exc:
                self._queue_error(reporting.ERROR_DISCONNECT_FAILED, detail=str(exc), exc=exc)

        if connected is None:
            self.queue.put(("disconnected", None))
            return
        self.ble_loop.submit(connected.disconnect(), callback=done)

    def browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select file",
            filetypes=[
                ("Supported", "*.png *.jpg *.jpeg *.gif *.bmp *.pdf *.txt"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.file_var.set(path)

    def browse_text_font(self) -> None:
        path = filedialog.askopenfilename(
            title="Select font",
            filetypes=[
                ("Fonts", "*.ttf *.otf *.ttc"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.text_font_var.set(path)

    def clear_text_font(self) -> None:
        self.text_font_var.set("")

    def _on_file_path_change(self, *_args) -> None:
        path = self.file_var.get()
        self._set_text_mode_for_path(path)
        self._update_option_sections(path)

    def _on_device_selection_changed(self, _event=None) -> None:
        self._reset_manual_model_if_target_changed()
        self._refresh_manual_model_controls()
        self._refresh_profile_label()
        self._refresh_paper_controls()

    def _on_manual_model_selection_changed(self, _event=None) -> None:
        self._refresh_manual_model_controls()
        self._refresh_profile_label()
        self._refresh_paper_controls()

    def _on_paper_selection_changed(self, _event=None) -> None:
        device = self.connected_device or self._effective_selected_device()
        self._configure_text_columns(device, reset=False)

    def _on_show_unknown_devices_changed(self) -> None:
        self._refresh_device_list()

    def _set_text_mode_for_path(self, path: str) -> None:
        path = path.strip()
        if not path:
            self.text_mode_var.set(False)
            return
        ext = os.path.splitext(path)[1].lower()
        self.text_mode_var.set(ext == ".txt")

    def _update_option_sections(self, path: str) -> None:
        ext = os.path.splitext(path.strip())[1].lower()
        self._set_section_visible(self.text_frame, ext == ".txt")
        self._set_section_visible(self.pdf_frame, ext == ".pdf")
        self._refresh_min_height()

    def _set_section_visible(self, frame: ttk.LabelFrame, visible: bool) -> None:
        if visible:
            if not frame.winfo_manager():
                frame.pack(before=self.action_frame, fill="x", padx=10, pady=10)
            return
        if frame.winfo_manager():
            frame.pack_forget()

    def _refresh_min_height(self) -> None:
        if not self._layout_ready:
            return
        self.update_idletasks()
        min_width, _min_height = self.minsize()
        req_height = self.winfo_reqheight()
        if req_height > 0:
            self.minsize(min_width, req_height)

    def _selected_device(self):
        label = self.device_var.get()
        return self.device_map.get(label)

    def _effective_selected_device(self):
        selected = self._selected_device()
        if isinstance(selected, PrinterDevice):
            return selected
        if isinstance(selected, ManualBluetoothSelection):
            model_key = self._selected_manual_model_key()
            if model_key is None:
                return None
            return self.catalog.device_from_model(
                model_key,
                display_name=selected.display_name,
                transport_target=selected.target.transport_target,
            )
        return None

    def _selected_manual_target(self) -> ManualBluetoothSelection | None:
        selected = self._selected_device()
        if isinstance(selected, ManualBluetoothSelection):
            return selected
        return None

    def _selected_manual_model_key(self) -> str | None:
        choice_map = self.__dict__.get("_manual_model_choice_map", {})
        var = self.__dict__.get("manual_model_var")
        if var is None:
            return None
        return choice_map.get(var.get())

    def _selected_manual_target_key(self) -> tuple[str, str] | None:
        target = self._selected_manual_target()
        if target is None:
            return None
        return (target.address.lower(), target.transport_badge)

    def _reset_manual_model_if_target_changed(self) -> None:
        target_key = self._selected_manual_target_key()
        previous_key = self.__dict__.get("_manual_model_target_key")
        if target_key != previous_key:
            self.manual_model_var.set("")
        self._manual_model_target_key = target_key

    def _refresh_profile_label(self) -> None:
        if self.connected_device:
            suffix = " (MANUAL)" if self.connected_device_was_manual else ""
            self.profile_var.set(f"{self.connected_device.profile_key.upper()}{suffix}")
            return
        if self._selected_manual_target() is not None and self._selected_manual_model_key() is None:
            self.profile_var.set("SELECT MODEL")
            return
        device = self._effective_selected_device()
        if device is not None:
            suffix = " (MANUAL)" if self._selected_manual_target() is not None else ""
            self.profile_var.set(f"{device.profile_key.upper()}{suffix}")
            return
        self.profile_var.set("")

    def _refresh_manual_model_controls(self) -> None:
        manual_target = self._selected_manual_target()
        visible = manual_target is not None and self.connected_device is None
        if visible:
            self.manual_model_label.grid()
            self.manual_model_combo.grid()
            self._set_widget_state(self.manual_model_combo, not self._connecting)
        else:
            self.manual_model_label.grid_remove()
            self.manual_model_combo.grid_remove()
        if not self.connected_device and not self._connecting:
            can_connect = manual_target is None or self._selected_manual_model_key() is not None
            self._set_connection_button("Connect", can_connect)
        self._refresh_min_height()

    def _build_manual_model_choice_map(self) -> dict[str, str]:
        choices: dict[str, str] = {}
        for model in sorted(self.catalog.models, key=lambda item: item.model_key):
            app_names = ", ".join(self.catalog.origin_app_names(model.origin_app_packages))
            names = ", ".join(model.names[:3])
            suffix = f" [{app_names}]" if app_names else ""
            choices[f"{model.model_key} - {names}{suffix}"] = model.model_key
        return choices

    @staticmethod
    def _paper_choices_for_device(device) -> tuple[tuple[str, str], ...]:
        if device is None:
            return ()
        presets = paper_presets_for_device(device)
        labels = [preset.label for preset in presets]
        duplicate_labels = {label for label in labels if labels.count(label) > 1}
        display_labels: list[str] = []
        for preset in presets:
            label = preset.label
            if label in duplicate_labels:
                label = f"{label} ({preset.render_width_px}px)"
            display_labels.append(label)
        duplicate_display_labels = {
            label for label in display_labels if display_labels.count(label) > 1
        }
        return tuple(
            (
                f"{label} [{preset.key}]" if label in duplicate_display_labels else label,
                preset.key,
            )
            for label, preset in zip(display_labels, presets)
        )

    @staticmethod
    def _default_paper_key_for_device(device) -> str | None:
        preset = default_paper_preset_for_device(device)
        if preset is None:
            return None
        return preset.key

    def _selected_paper_key(self) -> str | None:
        return self._paper_choice_map.get(self.paper_var.get())

    def _refresh_paper_controls(self) -> None:
        device = self.connected_device or self._effective_selected_device()
        selected_key = self._selected_paper_key()
        choices = self._paper_choices_for_device(device)
        self._paper_choice_map = dict(choices)
        display_by_key = {key: label for label, key in choices}
        labels = list(self._paper_choice_map.keys())
        self.paper_combo["values"] = labels
        if len(labels) > 1:
            target_key = selected_key or self._default_paper_key_for_device(device) or choices[0][1]
            self.paper_var.set(display_by_key.get(target_key, labels[0]))
            self.paper_label.grid()
            self.paper_combo.grid()
        else:
            self.paper_var.set("")
            self.paper_label.grid_remove()
            self.paper_combo.grid_remove()
        self._refresh_min_height()

    def print_file(self) -> None:
        path = self.file_var.get().strip()
        if not path:
            self._queue_error(reporting.ERROR_NO_FILE)
            return
        connected = self.connected_printer
        if connected is None:
            self._queue_error(reporting.ERROR_PROFILE_NOT_DETECTED)
            return
        ext = os.path.splitext(path)[1].lower()
        pdf_pages = None
        page_gap_mm = 0
        if ext == ".pdf":
            pdf_pages = self.pdf_pages_var.get().strip() or None
            page_gap_mm = int(self.pdf_gap_var.get())
        settings = PrintSettings(
            text_mode=self.text_mode_var.get(),
            rotate_90_clockwise=self.rotate_90_var.get(),
            blackening=self.darkness_var.get(),
            paper_preset_key=self._selected_paper_key(),
            text_font=self.text_font_var.get().strip() or None,
            text_columns=self.text_columns_var.get(),
            text_wrap=self.text_wrap_var.get(),
            trim_side_margins=self.trim_margins_var.get(),
            trim_top_bottom_margins=self.trim_top_bottom_margins_var.get(),
            pdf_pages=pdf_pages,
            page_gap_mm=page_gap_mm,
            debug_row_markers_interval=None,
        )
        def done(fut):
            try:
                fut.result()
                self._queue_status(reporting.STATUS_PRINT_SENT)
            except Exception as exc:
                self._queue_error(reporting.ERROR_PRINT_FAILED, detail=str(exc), exc=exc)

        async def run() -> None:
            self._queue_status(reporting.STATUS_PRINTING)
            await connected.print_file(path, settings=settings)

        self._queue_status(reporting.STATUS_PRINTING)
        self.ble_loop.submit(run(), callback=done)

    def _start_paper_motion(self, action: str) -> None:
        if action not in {"feed", "retract"}:
            return
        self._stop_paper_motion()
        if action == "feed":
            self._queue_status(reporting.STATUS_PAPER_FEED)
        else:
            self._queue_status(reporting.STATUS_PAPER_RETRACT)
        self._paper_motion_action = action
        self._send_paper_motion(action)
        self._schedule_paper_motion()

    def _schedule_paper_motion(self) -> None:
        if not self._paper_motion_action:
            return
        self._paper_motion_job = self.after(PAPER_MOTION_INTERVAL_MS, self._paper_motion_tick)

    def _paper_motion_tick(self) -> None:
        if not self._paper_motion_action:
            return
        self._send_paper_motion(self._paper_motion_action)
        self._schedule_paper_motion()

    def _stop_paper_motion(self, *_args) -> None:
        self._paper_motion_action = None
        if self._paper_motion_job is not None:
            self.after_cancel(self._paper_motion_job)
            self._paper_motion_job = None
        if not self._paper_motion_busy:
            self._restore_status_after_paper_motion()

    def _send_paper_motion(self, action: str) -> None:
        if self._paper_motion_busy:
            return
        connected_device = self.connected_device
        connected = self.connected_printer
        if not connected_device or connected is None:
            self._queue_error(reporting.ERROR_PROFILE_NOT_DETECTED)
            self._stop_paper_motion()
            return
        self._paper_motion_busy = True

        async def run() -> None:
            if action == "feed":
                self._queue_status(reporting.STATUS_PAPER_FEED)
                await connected.feed()
            else:
                self._queue_status(reporting.STATUS_PAPER_RETRACT)
                await connected.retract()

        def done(fut):
            self._paper_motion_busy = False
            try:
                fut.result()
                if not self._paper_motion_action:
                    self._restore_status_after_paper_motion()
            except Exception as exc:
                self._queue_error(reporting.ERROR_PAPER_MOTION_FAILED, detail=str(exc), exc=exc)
                self._stop_paper_motion()

        self.ble_loop.submit(run(), callback=done)

    def _restore_status_after_paper_motion(self) -> None:
        if self.__dict__.get("connected_device") is not None:
            self._queue_status(reporting.STATUS_CONNECT_DONE)
            return
        self._queue_status(reporting.STATUS_IDLE)

    def _set_connected_state(self, connected: bool, device=None) -> None:
        self._connecting = False
        self.connected_device = None
        self.connected_device_was_manual = False
        if connected and device:
            self.connected_device = device
            self.connected_device_was_manual = self._selected_manual_target() is not None
            suffix = " (MANUAL)" if self.connected_device_was_manual else ""
            self.profile_var.set(f"{device.profile_key.upper()}{suffix}")
            self._set_device_combo_state(False)
            self._set_widget_state(self.refresh_button, False)
            self._set_widget_state(self.file_entry, True)
            self._set_widget_state(self.browse_button, True)
            self._set_widget_state(self.text_mode_check, True)
            self._set_widget_state(self.rotate_90_check, True)
            self._set_widget_state(self.darkness_scale, True)
            self._set_widget_state(self.darkness_value_label, True)
            self._set_widget_state(self.paper_combo, True)
            self._set_widget_state(self.show_unknown_check, False)
            self._set_widget_state(self.manual_model_combo, False)
            self._set_widget_state(self.text_font_entry, True)
            self._set_widget_state(self.text_font_browse, True)
            self._set_widget_state(self.text_font_clear, True)
            self._set_widget_state(self.text_columns_scale, True)
            self._set_widget_state(self.text_columns_value_label, True)
            self._set_widget_state(self.text_wrap_check, True)
            self._set_widget_state(self.trim_margins_check, True)
            self._set_widget_state(self.trim_top_bottom_margins_check, True)
            self._set_widget_state(self.pdf_pages_entry, True)
            self._set_widget_state(self.pdf_gap_scale, True)
            self._set_widget_state(self.pdf_gap_value_label, True)
            self._set_widget_state(self.feed_button, True)
            self._set_widget_state(self.retract_button, True)
            self._set_widget_state(self.print_button, True)
            self._set_connection_button("Disconnect", True)
            self._refresh_manual_model_controls()
            self._refresh_paper_controls()
            self._configure_text_columns(device, reset=True)
            return

        self.profile_var.set("")
        self._set_device_combo_state(True)
        self._set_widget_state(self.refresh_button, True)
        self._set_widget_state(self.show_unknown_check, True)
        self._set_widget_state(self.file_entry, False)
        self._set_widget_state(self.browse_button, False)
        self._set_widget_state(self.text_mode_check, False)
        self._set_widget_state(self.rotate_90_check, False)
        self._set_widget_state(self.darkness_scale, False)
        self._set_widget_state(self.darkness_value_label, False)
        self._set_widget_state(self.paper_combo, False)
        self._set_widget_state(self.text_font_entry, False)
        self._set_widget_state(self.text_font_browse, False)
        self._set_widget_state(self.text_font_clear, False)
        self._set_widget_state(self.text_columns_scale, False)
        self._set_widget_state(self.text_columns_value_label, False)
        self._set_widget_state(self.text_wrap_check, False)
        self._set_widget_state(self.trim_margins_check, False)
        self._set_widget_state(self.trim_top_bottom_margins_check, False)
        self._set_widget_state(self.pdf_pages_entry, False)
        self._set_widget_state(self.pdf_gap_scale, False)
        self._set_widget_state(self.pdf_gap_value_label, False)
        self._set_widget_state(self.feed_button, False)
        self._set_widget_state(self.retract_button, False)
        self._set_widget_state(self.print_button, False)
        self._set_connection_button("Connect", True)
        self._stop_paper_motion()
        self._refresh_manual_model_controls()
        self._refresh_profile_label()
        self._refresh_paper_controls()

    def _configure_text_columns(self, device, *, reset: bool) -> None:
        if device is None:
            return
        paper_key = self._selected_paper_key()
        paper = device.profile.paper_preset(paper_key) if paper_key is not None else None
        if paper is None:
            paper = device.profile.default_paper_preset
        width = normalized_width(paper.render_width_px)
        default_columns = TextConverter.default_columns_for_width(width)
        min_columns = max(5, int(round(default_columns * 0.5)))
        max_columns = max(min_columns + 1, int(round(default_columns * 1.5)))
        self.text_columns_scale.configure(from_=min_columns, to=max_columns)
        if reset:
            self.text_columns_var.set(default_columns)
            return
        current_columns = self.text_columns_var.get()
        if current_columns < min_columns:
            self.text_columns_var.set(min_columns)
        elif current_columns > max_columns:
            self.text_columns_var.set(max_columns)

    def _set_connecting_state(self, connecting: bool) -> None:
        self._connecting = connecting
        if connecting:
            self._set_device_combo_state(False)
            self._set_widget_state(self.refresh_button, False)
            self._set_widget_state(self.show_unknown_check, False)
            self._set_widget_state(self.manual_model_combo, False)
            self._set_connection_button("Connecting...", False)
            return
        if self.connected_device:
            return
        self._set_device_combo_state(True)
        self._set_widget_state(self.refresh_button, True)
        self._set_widget_state(self.show_unknown_check, True)
        self._refresh_manual_model_controls()

    def _set_connection_button(self, label: str, enabled: bool) -> None:
        self.connection_button.configure(text=label)
        self._set_widget_state(self.connection_button, enabled)

    @staticmethod
    def _set_widget_state(widget, enabled: bool) -> None:
        if isinstance(widget, ttk.Widget):
            if enabled:
                widget.state(["!disabled"])
            else:
                widget.state(["disabled"])
            return
        state = "normal" if enabled else "disabled"
        widget.configure(state=state)

    def _set_device_combo_state(self, enabled: bool) -> None:
        state = "readonly" if enabled else "disabled"
        self.device_combo.configure(state=state)

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._stop_paper_motion()
        try:
            if self.connected_printer is not None:
                future = self.ble_loop.submit(self.connected_printer.disconnect())
                future.result(timeout=2.0)
        except Exception:
            pass
        finally:
            self.ble_loop.shutdown()
            self.destroy()


def main() -> int:
    app = TiMiniPrintGUI()
    app.mainloop()
    return 0
