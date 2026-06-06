from __future__ import annotations

import subprocess
import sys


def test_protocol_import_does_not_load_printing_layer() -> None:
    script = """
import sys
import timiniprint.protocol
loaded = sorted(name for name in sys.modules if name.startswith("timiniprint.printing"))
if loaded:
    raise SystemExit("\\n".join(loaded))
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
