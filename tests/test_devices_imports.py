from __future__ import annotations

import subprocess
import sys


def test_bluetooth_endpoint_resolver_import_does_not_load_transport_layer() -> None:
    script = """
import sys
from timiniprint.devices import BluetoothEndpointResolver
_ = BluetoothEndpointResolver
loaded = sorted(name for name in sys.modules if name.startswith("timiniprint.transport"))
if loaded:
    raise SystemExit("\\n".join(loaded))
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
