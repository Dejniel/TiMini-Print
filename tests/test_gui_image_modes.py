import tkinter as tk

import pytest

from timiniprint.app import gui as gui_module
from timiniprint.protocol.runtime import RuntimePrintCapabilities


class Printer:
    def __init__(self, capabilities=None):
        self.capabilities = capabilities

    def print_capabilities(self):
        return self.capabilities

@pytest.fixture
def app(monkeypatch):
    mapped_windows = []
    init_tk = tk.Tk.__init__

    def init_hidden_tk(window, *args, **kwargs):
        init_tk(window, *args, **kwargs)
        # Hide before the GUI constructor's first update_idletasks(), not after it.
        window.withdraw()
        window.bind("<Map>", lambda event: mapped_windows.append(window)
                    if event.widget is window else None, add="+")

    monkeypatch.setattr(tk.Tk, "__init__", init_hidden_tk)
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Requires a display (run with xvfb-run)")
    root.destroy()
    monkeypatch.setattr(gui_module, "emit_startup_warnings", lambda: None)
    monkeypatch.setattr(gui_module.TiMiniPrintGUI, "scan", lambda self: None)
    monkeypatch.setattr(gui_module.TiMiniPrintGUI, "_check_for_updates", lambda self: None)
    window = gui_module.TiMiniPrintGUI()
    try:
        yield window
    finally:
        window.connected_printer = None
        window._on_close()
    assert not mapped_windows, "GUI tests must not map windows onto the desktop"


def test_gui_remains_hidden_while_processing_layout_and_events(app):
    app.update_idletasks()
    app.update()
    assert app.state() == "withdrawn"
    assert not app.winfo_ismapped()


def connect(app, model, capabilities=None, *, variant=None):
    printer = Printer(capabilities)
    app.connected_printer = printer
    device = app.catalog.device_from_model(model)
    if variant is not None:
        device = device.with_protocol_variant(variant)
    app._set_connected_state(True, device)
    app.update_idletasks()
    return printer


def test_single_selector_defaults_and_preserves_explicit_choice(app):
    printer = connect(app, "v5x", RuntimePrintCapabilities(supports_gray=False))
    assert app.image_mode_var.get() == "Atkinson"
    printer.capabilities = RuntimePrintCapabilities(supports_gray=True)
    app._refresh_image_options()
    assert app.image_mode_var.get() == "Grayscale"
    app.image_mode_var.set("Bayer 4×4")
    app._on_image_mode_selected()
    app._refresh_paper_controls()
    assert app.image_mode_var.get() == "Bayer 4×4"
    assert "Automatic" not in app.image_mode_combo["values"]


def test_negative_gray_capability_replaces_unavailable_explicit_selection(app):
    printer = connect(app, "v5x")
    app.image_mode_var.set("Grayscale")
    app._on_image_mode_selected()
    printer.capabilities = RuntimePrintCapabilities(supports_gray=False)
    app._refresh_image_options()
    assert app.image_mode_var.get() == "Atkinson"
    assert "Grayscale" not in app.image_mode_combo["values"]
