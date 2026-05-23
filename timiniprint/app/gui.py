# TODO: DO NOT READ. This code is waiting to be rewritten :P
# One day I’ll refactor the whole GUI properly;
# for now, the terrible single-file monolith stays.

from __future__ import annotations

import asyncio
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .diagnostics import emit_startup_warnings
from .. import reporting
from ..devices import PrinterCatalog
from ..printing.builder import PrintJobBuilder
from ..printing.runtime.base import PreparedRuntimeContext
from ..printing.runtime.prepare import prepare_connection_runtime
from ..printing.send import send_prepared_job
from ..printing.settings import PrintSettings
from ..protocol import ImageEncoding, PaperMode, PrinterProtocol
from ..protocol.families import get_protocol_behavior
from ..rendering.converters.text import TextConverter
from ..transport.bluetooth import BleakBluetoothConnector, BluetoothDiscovery, BluetoothScanResult
from ..transport.bluetooth.types import DeviceInfo, DeviceTransport

PAPER_MOTION_INTERVAL_MS = 1000
DEBUG_AUTO_LABEL = "Auto"


class BleLoop:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        pending = [task for task in asyncio.all_tasks(self._loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        self._loop.close()

    def submit(self, coro, callback=None):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        if callback:
            future.add_done_callback(callback)
        return future

    def shutdown(self, timeout: float = 2.0) -> None:
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)


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
        self.connection = None
        self.runtime_context = PreparedRuntimeContext()

        self.devices = []
        self.device_map = {}
        self._unsupported_device_labels: set[str] = set()
        self._last_scan_result = BluetoothScanResult(devices=[], failures=[], raw_endpoints=[])

        self.device_var = tk.StringVar()
        self.profile_var = tk.StringVar(value="")
        self.debug_mode_var = tk.BooleanVar(value=False)
        self.debug_profile_var = tk.StringVar(value=DEBUG_AUTO_LABEL)
        self.debug_image_encoding_var = tk.StringVar(value=DEBUG_AUTO_LABEL)
        self.debug_paper_mode_var = tk.StringVar(value=DEBUG_AUTO_LABEL)
        self.debug_row_markers_var = tk.BooleanVar(value=False)
        self.debug_row_markers_interval_var = tk.IntVar(value=10)
        self.file_var = tk.StringVar()
        self.text_mode_var = tk.BooleanVar(value=False)
        self.rotate_90_var = tk.BooleanVar(value=False)
        self.darkness_var = tk.IntVar(value=3)
        self.paper_mode_var = tk.StringVar(value="")
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
        self._connecting = False
        self._paper_motion_action = None
        self._paper_motion_job = None
        self._paper_motion_busy = False
        self._layout_ready = False
        self._closing = False
        self._paper_mode_choice_map: dict[str, PaperMode] = {}
        self._debug_profile_choice_map: dict[str, str | None] = {}
        self._debug_image_encoding_choice_map: dict[str, ImageEncoding | None] = {}
        self._debug_paper_mode_choice_map: dict[str, PaperMode | None] = {}
        self.file_var.trace_add("write", self._on_file_path_change)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self.update_idletasks()
        self.minsize(int(self.winfo_reqwidth()*.9), self.winfo_reqheight())

        self._layout_ready = True
        self._set_connected_state(False)
        self.after(100, self._process_queue)
        self.after(200, self.scan)
        
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
        self.debug_mode_check = ttk.Checkbutton(
            device_frame,
            text="Debug only for programmers",
            variable=self.debug_mode_var,
            command=self._on_debug_mode_changed,
        )
        self.debug_mode_check.grid(row=1, column=2, sticky="e", **padding)

        self.connection_button = ttk.Button(device_frame, text="Connect", command=self.toggle_connection)
        self.connection_button.grid(row=1, column=3, sticky="e", **padding)

        self.debug_frame = ttk.LabelFrame(self, text="Debug overrides")
        self.debug_frame.columnconfigure(1, weight=1)
        ttk.Label(self.debug_frame, text="Force profile:").grid(row=0, column=0, sticky="w", **padding)
        self.debug_profile_combo = ttk.Combobox(
            self.debug_frame,
            textvariable=self.debug_profile_var,
            width=48,
            state="readonly",
        )
        self.debug_profile_combo.grid(row=0, column=1, sticky="ew", **padding)
        self.debug_profile_combo.bind("<<ComboboxSelected>>", self._on_debug_override_changed)
        ttk.Label(self.debug_frame, text="Force image encoding:").grid(row=1, column=0, sticky="w", **padding)
        self.debug_image_encoding_combo = ttk.Combobox(
            self.debug_frame,
            textvariable=self.debug_image_encoding_var,
            width=48,
            state="readonly",
        )
        self.debug_image_encoding_combo.grid(row=1, column=1, sticky="ew", **padding)
        self.debug_image_encoding_combo.bind("<<ComboboxSelected>>", self._on_debug_override_changed)
        ttk.Label(self.debug_frame, text="Force paper mode:").grid(row=2, column=0, sticky="w", **padding)
        self.debug_paper_mode_combo = ttk.Combobox(
            self.debug_frame,
            textvariable=self.debug_paper_mode_var,
            width=48,
            state="readonly",
        )
        self.debug_paper_mode_combo.grid(row=2, column=1, sticky="ew", **padding)
        self.debug_paper_mode_combo.bind("<<ComboboxSelected>>", self._on_debug_override_changed)
        marker_frame = ttk.Frame(self.debug_frame)
        marker_frame.grid(row=3, column=0, columnspan=2, sticky="w", **padding)
        self.debug_row_markers_check = ttk.Checkbutton(
            marker_frame,
            text="Add debug row markers every",
            variable=self.debug_row_markers_var,
        )
        self.debug_row_markers_check.pack(side="left")
        self.debug_row_markers_spin = tk.Spinbox(
            marker_frame,
            from_=1,
            to=500,
            width=5,
            textvariable=self.debug_row_markers_interval_var,
        )
        self.debug_row_markers_spin.pack(side="left", padx=(6, 6))
        ttk.Label(marker_frame, text="rows").pack(side="left")

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
        self.paper_mode_label = ttk.Label(options_frame, text="Paper mode:")
        self.paper_mode_label.grid(row=2, column=0, sticky="w", **padding)
        self.paper_mode_combo = ttk.Combobox(
            options_frame,
            textvariable=self.paper_mode_var,
            width=24,
            state="readonly",
        )
        self.paper_mode_combo.grid(row=2, column=1, sticky="w", **padding)
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
        self.retract_button = ttk.Button(self.action_frame, text="Retract")
        self.feed_button = ttk.Button(self.action_frame, text="Feed")
        self.feed_button.pack(side="left")
        self.retract_button.pack(side="left", padx=(6, 0))
        self.print_button.pack(side="right")
        self.feed_button.bind("<ButtonPress-1>", lambda event: self._start_paper_motion("feed"))
        self.feed_button.bind("<ButtonRelease-1>", self._stop_paper_motion)
        self.feed_button.bind("<Leave>", self._stop_paper_motion)
        self.retract_button.bind("<ButtonPress-1>", lambda event: self._start_paper_motion("retract"))
        self.retract_button.bind("<ButtonRelease-1>", self._stop_paper_motion)
        self.retract_button.bind("<Leave>", self._stop_paper_motion)

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=10, pady=10)
        ttk.Label(status_frame, text="Status:").pack(side="left")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left", padx=6)

        self._update_option_sections(self.file_var.get())
        self._refresh_paper_mode_controls()
        self._refresh_debug_controls()

    def _process_queue(self) -> None:
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
        self.after(100, self._process_queue)

    def _device_label(self, device) -> str:
        name = device.display_name or ""
        transport = f" {device.transport_badge}"
        status = " [unpaired]" if device.paired is False else ""
        if name:
            return f"{name} ({device.address}){transport}{status}"
        return f"{device.address}{transport}{status}"

    @staticmethod
    def _unsupported_endpoint_label(endpoint: DeviceInfo) -> str:
        name = endpoint.name or endpoint.address or "<unknown>"
        address = endpoint.address or "<unknown>"
        transport = f"[{endpoint.transport.value}]"
        status = " [unpaired]" if endpoint.paired is False else ""
        if endpoint.name:
            return f"{name} ({address}) {transport} [unsupported]{status}"
        return f"{address} {transport} [unsupported]{status}"

    def _refresh_device_list(self) -> None:
        result = self._last_scan_result
        devices = list(result.devices)
        self.devices = devices
        self.device_map = {self._device_label(device): device for device in devices}
        self._unsupported_device_labels = set()

        labels = list(self.device_map.keys())
        if self.debug_mode_var.get():
            for endpoint in self._unsupported_endpoints(result.raw_endpoints):
                label = self._unsupported_endpoint_label(endpoint)
                self._unsupported_device_labels.add(label)
                labels.append(label)

        self.device_combo["values"] = labels
        current = self.device_var.get()
        if labels:
            if current in labels:
                self.device_var.set(current)
            elif not self.connected_device:
                self.device_var.set(labels[0])
        else:
            self.device_var.set("")
        self._refresh_profile_label()
        self._refresh_paper_mode_controls()
        self._refresh_debug_controls()

    def _unsupported_endpoints(self, endpoints: list[DeviceInfo]) -> list[DeviceInfo]:
        unsupported = []
        for endpoint in endpoints:
            if self.catalog.detect_device(endpoint.name or "", endpoint.address) is None:
                unsupported.append(endpoint)
        return unsupported

    def _unsupported_endpoint_for_label(self, label: str) -> DeviceInfo | None:
        for endpoint in self._unsupported_endpoints(self._last_scan_result.raw_endpoints):
            if self._unsupported_endpoint_label(endpoint) == label:
                return endpoint
        return None

    def _queue_status(self, key: str, **ctx) -> None:
        self.reporter.status(key, **ctx)

    def _queue_warning(self, key: str, detail=None, **ctx) -> None:
        self.reporter.warning(key, detail=detail, **ctx)

    def _queue_error(self, key: str, detail=None, exc=None, **ctx) -> None:
        self.reporter.error(key, detail=detail, exc=exc, **ctx)

    def scan(self) -> None:
        self._queue_status(reporting.STATUS_SCAN_START)

        def done(fut):
            try:
                result = fut.result()
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
                self._queue_error(reporting.ERROR_SCAN_FAILED, detail=str(exc), exc=exc)

        self.ble_loop.submit(self.discovery.scan_report(), callback=done)

    def _scan_result_status_count(self, result: BluetoothScanResult) -> int:
        if self.debug_mode_var.get():
            return len(result.raw_endpoints)
        return len(result.devices)

    def connect(self) -> None:
        label = self.device_var.get()
        device = self._effective_selected_device()
        if not device:
            if label in self._unsupported_device_labels:
                self._queue_error(reporting.ERROR_UNSUPPORTED_DEVICE)
                return
            self._queue_error(reporting.ERROR_NO_DEVICE)
            return
        self._queue_status(reporting.STATUS_CONNECT_START)
        self.queue.put(("connecting", True))

        def done(fut):
            try:
                self.connection, self.runtime_context = fut.result()
                self._queue_status(reporting.STATUS_CONNECT_DONE)
                self.queue.put(("connected", device))
            except Exception as exc:
                self._queue_error(reporting.ERROR_CONNECT_FAILED, detail=str(exc), exc=exc)
                self.queue.put(("connecting", False))

        async def run():
            connection = await self.connector.connect(device)
            try:
                runtime_context = await prepare_connection_runtime(
                    device,
                    connection,
                    reporter=self.reporter,
                )
            except Exception:
                try:
                    await connection.disconnect()
                except Exception:
                    pass
                raise
            return connection, runtime_context

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
        connection = self.connection

        def done(fut):
            try:
                fut.result()
                self.connection = None
                self.runtime_context = PreparedRuntimeContext()
                self._queue_status(reporting.STATUS_DISCONNECT_DONE)
                self.queue.put(("disconnected", None))
            except Exception as exc:
                self._queue_error(reporting.ERROR_DISCONNECT_FAILED, detail=str(exc), exc=exc)

        if connection is None:
            self.queue.put(("disconnected", None))
            return
        self.ble_loop.submit(connection.disconnect(), callback=done)

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
        self._refresh_profile_label()
        self._refresh_paper_mode_controls()
        self._refresh_debug_controls()

    def _on_debug_mode_changed(self) -> None:
        levels = {"warning", "error"}
        if self.debug_mode_var.get():
            levels.add("debug")
        self._stderr_sink.set_levels(levels)
        self._refresh_device_list()
        self._set_debug_panel_visible(self.debug_mode_var.get())
        self._refresh_debug_widget_states()
        self._refresh_debug_controls()

    def _on_debug_override_changed(self, _event=None) -> None:
        self._refresh_profile_label()
        self._refresh_paper_mode_controls()
        self._refresh_debug_controls()

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

    def _selected_debug_profile_key(self) -> str | None:
        if not self.debug_mode_var.get():
            return None
        return self._debug_profile_choice_map.get(self.debug_profile_var.get())

    def _selected_image_encoding_override(self) -> ImageEncoding | None:
        if not self.debug_mode_var.get():
            return None
        return self._debug_image_encoding_choice_map.get(self.debug_image_encoding_var.get())

    def _selected_debug_paper_mode(self) -> PaperMode | None:
        if not self.debug_mode_var.get():
            return None
        return self._debug_paper_mode_choice_map.get(self.debug_paper_mode_var.get())

    def _selected_debug_row_markers_interval(self) -> int | None:
        if not self.debug_mode_var.get() or not self.debug_row_markers_var.get():
            return None
        try:
            return max(1, int(self.debug_row_markers_interval_var.get()))
        except Exception:
            return 10

    def _effective_selected_device(self):
        base_device = self._selected_device()
        profile_key = self._selected_debug_profile_key()
        if profile_key is None:
            return base_device

        label = self.device_var.get()
        endpoint = self._unsupported_endpoint_for_label(label)
        if base_device is not None:
            display_name = base_device.display_name
            transport_target = base_device.transport_target
        elif endpoint is not None:
            display_name = endpoint.name or endpoint.address
            transport_target = self.discovery.transport_target_from_endpoint(endpoint)
        else:
            return None
        return self.catalog.device_from_profile(
            profile_key,
            display_name=display_name,
            transport_target=transport_target,
        )

    def _refresh_profile_label(self) -> None:
        if self.connected_device:
            self.profile_var.set(self.connected_device.profile_key.upper())
            return
        label = self.device_var.get()
        device = self._effective_selected_device()
        if device is not None:
            self.profile_var.set(device.profile_key.upper())
            return
        if label in self._unsupported_device_labels:
            self.profile_var.set("Unsupported (debug only)")
            return
        self.profile_var.set("")

    def _set_debug_panel_visible(self, visible: bool) -> None:
        if visible:
            if not self.debug_frame.winfo_manager():
                self.debug_frame.pack(before=self.file_frame, fill="x", padx=10, pady=10)
            self._refresh_min_height()
            return
        if self.debug_frame.winfo_manager():
            self.debug_frame.pack_forget()
        self._refresh_min_height()

    def _refresh_debug_controls(self) -> None:
        self._refresh_debug_profile_choices()
        self._refresh_debug_image_encoding_choices()
        self._refresh_debug_paper_mode_choices()
        self._refresh_debug_widget_states()

    def _refresh_debug_widget_states(self) -> None:
        debug_enabled = self.debug_mode_var.get()
        profile_enabled = debug_enabled and self.connected_device is None
        self._set_widget_state(self.debug_profile_combo, profile_enabled)
        self._set_widget_state(self.debug_image_encoding_combo, debug_enabled)
        self._set_widget_state(self.debug_paper_mode_combo, debug_enabled)
        self._set_widget_state(self.debug_row_markers_check, debug_enabled)
        self._set_widget_state(self.debug_row_markers_spin, debug_enabled)

    def _refresh_debug_profile_choices(self) -> None:
        current_key = self._selected_debug_profile_key()
        selected_device = self._selected_device()
        choices: list[tuple[str, str | None]] = [(DEBUG_AUTO_LABEL, None)]
        for profile in self.catalog.profiles:
            suffix = f"[{profile.default_protocol_family.value}]"
            if selected_device is not None and profile.profile_key == selected_device.profile_key:
                suffix = f"{suffix} [detected]"
            choices.append((f"{profile.profile_key} {suffix}", profile.profile_key))
        self._debug_profile_choice_map = dict(choices)
        self.debug_profile_combo["values"] = [label for label, _value in choices]
        self._restore_debug_choice(
            self.debug_profile_var,
            self._debug_profile_choice_map,
            current_key,
        )

    def _refresh_debug_image_encoding_choices(self) -> None:
        current_encoding = self._selected_image_encoding_override()
        device = self.connected_device or self._effective_selected_device()
        choices: list[tuple[str, ImageEncoding | None]] = [(DEBUG_AUTO_LABEL, None)]
        if device is not None:
            compatible = get_protocol_behavior(device.protocol_family).image_encoding_support.keys()
            for encoding in compatible:
                choices.append((f"{encoding.value} [compatible]", encoding))
        self._debug_image_encoding_choice_map = dict(choices)
        self.debug_image_encoding_combo["values"] = [label for label, _value in choices]
        self._restore_debug_choice(
            self.debug_image_encoding_var,
            self._debug_image_encoding_choice_map,
            current_encoding,
        )

    def _refresh_debug_paper_mode_choices(self) -> None:
        current_mode = self._selected_debug_paper_mode()
        device = self.connected_device or self._effective_selected_device()
        choices: list[tuple[str, PaperMode | None]] = [(DEBUG_AUTO_LABEL, None)]
        if device is not None:
            for mode in PrinterProtocol(device).supported_paper_modes():
                choices.append((f"{mode.label} ({mode.value}) [compatible]", mode))
        self._debug_paper_mode_choice_map = dict(choices)
        self.debug_paper_mode_combo["values"] = [label for label, _value in choices]
        self._restore_debug_choice(
            self.debug_paper_mode_var,
            self._debug_paper_mode_choice_map,
            current_mode,
        )

    @staticmethod
    def _restore_debug_choice(choice_var, choice_map: dict[str, object], selected_value: object) -> None:
        for label, value in choice_map.items():
            if value == selected_value:
                choice_var.set(label)
                return
        choice_var.set(DEBUG_AUTO_LABEL)

    @staticmethod
    def _paper_mode_choices_for_device(device) -> tuple[tuple[str, PaperMode], ...]:
        if device is None:
            return ()
        return tuple((mode.label, mode) for mode in PrinterProtocol(device).supported_paper_modes())

    @staticmethod
    def _default_paper_mode_label_for_device(device) -> str | None:
        if device is None or device.profile.default_paper_mode is None:
            return None
        supported_modes = {mode for _label, mode in TiMiniPrintGUI._paper_mode_choices_for_device(device)}
        if device.profile.default_paper_mode not in supported_modes:
            return None
        return device.profile.default_paper_mode.label

    def _selected_paper_mode(self) -> PaperMode | None:
        debug_paper_mode = self._selected_debug_paper_mode()
        if debug_paper_mode is not None:
            return debug_paper_mode
        return self._paper_mode_choice_map.get(self.paper_mode_var.get())

    def _refresh_paper_mode_controls(self) -> None:
        device = self.connected_device or self._effective_selected_device()
        choices = self._paper_mode_choices_for_device(device)
        self._paper_mode_choice_map = dict(choices)
        labels = [label for label, _mode in choices]
        self.paper_mode_combo["values"] = labels
        if labels:
            if self.paper_mode_var.get() not in self._paper_mode_choice_map:
                self.paper_mode_var.set(
                    self._default_paper_mode_label_for_device(device) or labels[0]
                )
            self.paper_mode_label.grid()
            self.paper_mode_combo.grid()
        else:
            self.paper_mode_var.set("")
            self.paper_mode_label.grid_remove()
            self.paper_mode_combo.grid_remove()
        self._refresh_min_height()

    def print_file(self) -> None:
        path = self.file_var.get().strip()
        if not path:
            self._queue_error(reporting.ERROR_NO_FILE)
            return
        connected_device = self.connected_device
        if not connected_device or self.connection is None:
            self._queue_error(reporting.ERROR_PROFILE_NOT_DETECTED)
            return
        ext = os.path.splitext(path)[1].lower()
        pdf_pages = None
        pdf_page_gap_mm = 0
        if ext == ".pdf":
            pdf_pages = self.pdf_pages_var.get().strip() or None
            pdf_page_gap_mm = int(self.pdf_gap_var.get())
        settings = PrintSettings(
            text_mode=self.text_mode_var.get(),
            rotate_90_clockwise=self.rotate_90_var.get(),
            blackening=self.darkness_var.get(),
            paper_mode=self._selected_paper_mode(),
            text_font=self.text_font_var.get().strip() or None,
            text_columns=self.text_columns_var.get(),
            text_wrap=self.text_wrap_var.get(),
            trim_side_margins=self.trim_margins_var.get(),
            trim_top_bottom_margins=self.trim_top_bottom_margins_var.get(),
            pdf_pages=pdf_pages,
            pdf_page_gap_mm=pdf_page_gap_mm,
            image_encoding_override=self._selected_image_encoding_override(),
            debug_row_markers_interval=self._selected_debug_row_markers_interval(),
        )
        builder = PrintJobBuilder(
            connected_device,
            settings=settings,
            runtime_context=self.runtime_context,
        )

        def done(fut):
            try:
                fut.result()
                self._queue_status(reporting.STATUS_PRINT_SENT)
            except Exception as exc:
                self._queue_error(reporting.ERROR_PRINT_FAILED, detail=str(exc), exc=exc)

        async def run() -> None:
            self._queue_status(reporting.STATUS_PRINTING)
            job = builder.build_from_file(path)
            await send_prepared_job(
                connected_device,
                self.connection,
                job,
                reporter=self.reporter,
            )

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
        if not connected_device or self.connection is None:
            self._queue_error(reporting.ERROR_PROFILE_NOT_DETECTED)
            self._stop_paper_motion()
            return
        job = PrinterProtocol(connected_device).build_paper_motion(action)
        self._paper_motion_busy = True

        async def run() -> None:
            if action == "feed":
                self._queue_status(reporting.STATUS_PAPER_FEED)
            else:
                self._queue_status(reporting.STATUS_PAPER_RETRACT)
            await self.connection.send(job)

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
        if connected and device:
            self.connected_device = device
            self.profile_var.set(device.profile_key.upper())
            self._set_device_combo_state(False)
            self._set_widget_state(self.refresh_button, False)
            self._set_widget_state(self.file_entry, True)
            self._set_widget_state(self.browse_button, True)
            self._set_widget_state(self.text_mode_check, True)
            self._set_widget_state(self.rotate_90_check, True)
            self._set_widget_state(self.darkness_scale, True)
            self._set_widget_state(self.darkness_value_label, True)
            self._set_widget_state(self.paper_mode_combo, True)
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
            self._configure_text_columns(device.profile)
            self._refresh_paper_mode_controls()
            self._refresh_debug_controls()
            self._refresh_debug_widget_states()
            return

        self.profile_var.set("")
        self._set_device_combo_state(True)
        self._set_widget_state(self.refresh_button, True)
        self._set_widget_state(self.file_entry, False)
        self._set_widget_state(self.browse_button, False)
        self._set_widget_state(self.text_mode_check, False)
        self._set_widget_state(self.rotate_90_check, False)
        self._set_widget_state(self.darkness_scale, False)
        self._set_widget_state(self.darkness_value_label, False)
        self._set_widget_state(self.paper_mode_combo, False)
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
        self._refresh_profile_label()
        self._refresh_paper_mode_controls()
        self._refresh_debug_controls()
        self._refresh_debug_widget_states()

    def _configure_text_columns(self, profile) -> None:
        width = self._normalized_width(profile.width)
        default_columns = TextConverter.default_columns_for_width(width)
        min_columns = max(5, int(round(default_columns * 0.5)))
        max_columns = max(min_columns + 1, int(round(default_columns * 1.5)))
        self.text_columns_scale.configure(from_=min_columns, to=max_columns)
        self.text_columns_var.set(default_columns)

    @staticmethod
    def _normalized_width(width: int) -> int:
        if width % 8 == 0:
            return width
        return width - (width % 8)

    def _set_connecting_state(self, connecting: bool) -> None:
        self._connecting = connecting
        if connecting:
            self._set_device_combo_state(False)
            self._set_widget_state(self.refresh_button, False)
            self._set_connection_button("Connecting...", False)
            return
        if self.connected_device:
            return
        self._set_device_combo_state(True)
        self._set_widget_state(self.refresh_button, True)
        self._set_connection_button("Connect", True)

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
            if self.connection is not None:
                future = self.ble_loop.submit(self.connection.disconnect())
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
