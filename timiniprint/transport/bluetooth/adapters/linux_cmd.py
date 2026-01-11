from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional, Set, Tuple

from ..types import DeviceInfo


class LinuxCommandTools:
    def scan_devices(self, timeout: float) -> Tuple[List[DeviceInfo], Optional[Set[str]]]:
        if not self._has_bluetoothctl():
            return [], None
        timeout_s = max(1, int(timeout))
        self._run_bluetoothctl(["--timeout", str(timeout_s), "scan", "on"], timeout=timeout_s)
        devices_output = self._run_bluetoothctl(["devices"])
        if devices_output is None:
            return [], None
        paired_output = self._run_bluetoothctl(["devices", "Paired"]) or ""
        paired_addresses = {
            line.split(" ", 2)[1]
            for line in paired_output.splitlines()
            if line.strip().startswith("Device ") and len(line.split(" ", 2)) > 1
        }
        devices = []
        for line in devices_output.splitlines():
            line = line.strip()
            if not line.startswith("Device "):
                continue
            parts = line.split(" ", 2)
            if len(parts) < 2:
                continue
            address = parts[1]
            name = parts[2] if len(parts) > 2 else ""
            paired = address in paired_addresses
            devices.append(DeviceInfo(name=name, address=address, paired=paired))
        return DeviceInfo.dedupe(devices), paired_addresses

    def resolve_rfcomm_channel(self, address: str) -> Optional[int]:
        if not shutil.which("sdptool"):
            return None
        try:
            result = subprocess.run(
                ["sdptool", "browse", address],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                text=True,
            )
        except Exception:
            return None
        output = result.stdout or ""
        channel = None
        seen_serial = False
        for raw in output.splitlines():
            line = raw.strip()
            if line.startswith("Service Name:"):
                name = line.split(":", 1)[-1].strip().lower()
                seen_serial = any(key in name for key in ("serial", "spp", "printer"))
            elif line.startswith("Channel:"):
                try:
                    value = int(line.split(":", 1)[-1].strip())
                except ValueError:
                    value = None
                if value is None:
                    continue
                if seen_serial:
                    return value
                if channel is None:
                    channel = value
                seen_serial = False
            elif not line:
                seen_serial = False
        return channel

    def ensure_paired(self, address: str) -> None:
        if not self._has_bluetoothctl():
            return
        if self._bluetoothctl_is_paired(address):
            return
        self._bluetoothctl_pair(address)
        self._bluetoothctl_trust(address)
        if not self._bluetoothctl_is_paired(address):
            raise RuntimeError("pairing did not complete")

    @staticmethod
    def _has_bluetoothctl() -> bool:
        return bool(shutil.which("bluetoothctl"))

    @staticmethod
    def _run_bluetoothctl(args: List[str], timeout: Optional[float] = None) -> Optional[str]:
        if not shutil.which("bluetoothctl"):
            return None
        try:
            result = subprocess.run(
                ["bluetoothctl"] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                text=True,
                timeout=timeout,
            )
        except Exception:
            return None
        return result.stdout or ""

    def _bluetoothctl_is_paired(self, address: str) -> bool:
        output = self._run_bluetoothctl(["info", address], timeout=5)
        if not output:
            return False
        for line in output.splitlines():
            line = line.strip().lower()
            if line.startswith("paired:"):
                return line.split(":", 1)[-1].strip() == "yes"
        return False

    def _bluetoothctl_pair(self, address: str, timeout: float = 15.0) -> None:
        if not self._has_bluetoothctl():
            return
        result = subprocess.run(
            ["bluetoothctl", "pair", address],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(msg or "pairing failed")

    def _bluetoothctl_trust(self, address: str) -> None:
        self._run_bluetoothctl(["trust", address], timeout=5)
