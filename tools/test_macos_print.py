#!/usr/bin/env python3
"""Test script for macOS BLE printing.

This script scans for BLE devices, lets you select one, and sends a test print.
"""
from __future__ import annotations

import asyncio
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timiniprint.transport.bluetooth import SppBackend, DeviceInfo
from timiniprint.devices import PrinterModelRegistry, DeviceResolver
from timiniprint.protocol import Raster, build_job_from_raster


def create_test_pattern(width: int = 384, margin: int = 50) -> Raster:
    """Create a simple test pattern for printing.
    
    Creates a pattern with:
    - Whitespace margins before and after
    - A border
    - Diagonal lines
    - "TEST" text approximation using pixels
    - A gradient bar
    
    Args:
        width: Width of the pattern in pixels
        margin: Blank lines to add before and after the pattern
    """
    content_height = 200
    height = content_height + (margin * 2)  # Add margin top and bottom
    pixels = [0] * (width * height)
    
    # Offset all drawing by the top margin
    y_offset = margin
    
    def set_pixel(x: int, y: int, value: int = 1) -> None:
        """Set a pixel, automatically offset by top margin."""
        actual_y = y + y_offset
        if 0 <= x < width and 0 <= actual_y < height:
            pixels[actual_y * width + x] = value
    
    def draw_rect(x1: int, y1: int, x2: int, y2: int, filled: bool = False) -> None:
        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                if filled or x == x1 or x == x2 or y == y1 or y == y2:
                    set_pixel(x, y)
    
    # Draw border (around content area, not margins)
    draw_rect(0, 0, width - 1, content_height - 1)
    draw_rect(2, 2, width - 3, content_height - 3)
    
    # Draw diagonal lines in corners
    for i in range(30):
        # Top-left
        set_pixel(10 + i, 10 + i)
        set_pixel(11 + i, 10 + i)
        # Top-right
        set_pixel(width - 11 - i, 10 + i)
        set_pixel(width - 12 - i, 10 + i)
        # Bottom-left
        set_pixel(10 + i, content_height - 11 - i)
        set_pixel(11 + i, content_height - 11 - i)
        # Bottom-right
        set_pixel(width - 11 - i, content_height - 11 - i)
        set_pixel(width - 12 - i, content_height - 11 - i)
    
    # Draw "TEST" text in center (simple pixel font)
    text_y = 70
    text_x = width // 2 - 60
    
    # T
    for x in range(text_x, text_x + 20):
        set_pixel(x, text_y)
        set_pixel(x, text_y + 1)
    for y in range(text_y, text_y + 30):
        set_pixel(text_x + 9, y)
        set_pixel(text_x + 10, y)
    
    # E
    text_x += 25
    for y in range(text_y, text_y + 30):
        set_pixel(text_x, y)
        set_pixel(text_x + 1, y)
    for x in range(text_x, text_x + 15):
        set_pixel(x, text_y)
        set_pixel(x, text_y + 1)
        set_pixel(x, text_y + 14)
        set_pixel(x, text_y + 15)
        set_pixel(x, text_y + 28)
        set_pixel(x, text_y + 29)
    
    # S
    text_x += 20
    for x in range(text_x, text_x + 15):
        set_pixel(x, text_y)
        set_pixel(x, text_y + 1)
        set_pixel(x, text_y + 14)
        set_pixel(x, text_y + 15)
        set_pixel(x, text_y + 28)
        set_pixel(x, text_y + 29)
    for y in range(text_y, text_y + 15):
        set_pixel(text_x, y)
        set_pixel(text_x + 1, y)
    for y in range(text_y + 15, text_y + 30):
        set_pixel(text_x + 13, y)
        set_pixel(text_x + 14, y)
    
    # T
    text_x += 20
    for x in range(text_x, text_x + 20):
        set_pixel(x, text_y)
        set_pixel(x, text_y + 1)
    for y in range(text_y, text_y + 30):
        set_pixel(text_x + 9, y)
        set_pixel(text_x + 10, y)
    
    # Draw gradient bar at bottom
    bar_y = 140
    bar_height = 20
    for y in range(bar_y, bar_y + bar_height):
        for x in range(20, width - 20):
            # Create dithered gradient
            density = (x - 20) / (width - 40)
            if (x + y) % max(1, int(10 * (1 - density))) == 0:
                set_pixel(x, y)
    
    # Draw some test lines
    line_y = 170
    # Solid line
    for x in range(20, width - 20):
        set_pixel(x, line_y)
    # Dotted line
    for x in range(20, width - 20, 4):
        set_pixel(x, line_y + 5)
        set_pixel(x + 1, line_y + 5)
    # Dashed line
    for x in range(20, width - 20, 10):
        for dx in range(6):
            set_pixel(x + dx, line_y + 10)
    
    return Raster(pixels=pixels, width=width)


async def scan_devices() -> list[DeviceInfo]:
    """Scan for nearby BLE devices."""
    print("Scanning for Bluetooth devices (10 seconds)...")
    devices = await SppBackend.scan(timeout=10.0)
    return devices


def select_device(devices: list[DeviceInfo]) -> DeviceInfo | None:
    """Let user select a device from the list."""
    if not devices:
        print("No devices found.")
        return None
    
    print(f"\nFound {len(devices)} device(s):\n")
    for i, device in enumerate(devices):
        name = device.name or "(unnamed)"
        print(f"  [{i + 1}] {name}")
        print(f"      Address: {device.address}")
        if device.paired is not None:
            print(f"      Paired: {'Yes' if device.paired else 'No'}")
        print()
    
    while True:
        try:
            choice = input("Select device number (or 'q' to quit): ").strip()
            if choice.lower() == 'q':
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(devices):
                return devices[idx]
            print(f"Please enter a number between 1 and {len(devices)}")
        except ValueError:
            print("Please enter a valid number")
        except KeyboardInterrupt:
            return None


async def test_print(device: DeviceInfo, model_name: str | None = None) -> None:
    """Connect to device and send a test print."""
    registry = PrinterModelRegistry.load()
    resolver = DeviceResolver(registry)
    
    # Try to resolve the printer model
    try:
        match = resolver.resolve_model_with_origin(device.name or "", model_name, device.address)
        model = match.model
        print(f"Detected printer model: {model.model_no}")
    except Exception as e:
        print(f"Could not detect printer model: {e}")
        print("Using default settings (384px width, 203 DPI)")
        # Create a minimal model for testing
        model = type('Model', (), {
            'width': 384,
            'dev_dpi': 203,
            'img_print_speed': 6,
            'moderation_energy': 5000,
            'new_compress': True,
            'new_format': False,
            'a4xii': False,
            'img_mtu': 180,
            'interval_ms': 4,
        })()
    
    # Create test pattern
    width = model.width - (model.width % 8)
    print(f"Creating test pattern ({width}px wide)...")
    raster = create_test_pattern(width)
    
    # Build print job
    print("Building print job...")
    data = build_job_from_raster(
        raster,
        is_text=False,
        speed=model.img_print_speed,
        energy=model.moderation_energy or 5000,
        blackening=3,
        compress=model.new_compress,
        lsb_first=not model.a4xii,
        new_format=model.new_format,
        feed_padding=12,
        dev_dpi=model.dev_dpi,
    )
    
    print(f"Print job size: {len(data)} bytes")
    
    # Connect and print
    backend = SppBackend()
    
    print(f"\nConnecting to {device.name or device.address}...")
    try:
        await backend.connect(device.address)
        print("Connected!")
    except Exception as e:
        print(f"Connection failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    try:
        print("Sending test print...")
        print(f"  Data size: {len(data)} bytes")
        
        # For BLE, use smaller chunks and longer intervals
        # BLE thermal printers need more time than classic Bluetooth SPP
        mtu = 20  # Small chunks for BLE reliability
        interval = 50  # 50ms between chunks (BLE needs more time)
        
        print(f"  Chunk size: {mtu} bytes")
        print(f"  Interval: {interval}ms")
        
        total_chunks = (len(data) + mtu - 1) // mtu
        print(f"  Total chunks: {total_chunks}")
        estimated_time = total_chunks * interval / 1000
        print(f"  Estimated time: {estimated_time:.1f} seconds")
        print()
        
        # Send with progress indication
        start_time = asyncio.get_event_loop().time()
        await backend.write(data, mtu, interval)
        elapsed = asyncio.get_event_loop().time() - start_time
        
        print(f"\nPrint job sent successfully in {elapsed:.1f} seconds!")
    except Exception as e:
        print(f"\nPrint failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Disconnecting...")
        await backend.disconnect()
        print("Done!")


async def main() -> int:
    print("=" * 50)
    print("  TiMini Print - macOS BLE Test Script")
    print("=" * 50)
    print()
    
    # Check platform
    if sys.platform != "darwin":
        print(f"Warning: This script is designed for macOS, but running on {sys.platform}")
        print()
    
    # Check bleak availability
    try:
        import bleak
        # print(f"Using bleak version: {bleak.__version__}")
    except ImportError:
        print("Error: bleak is not installed. Install it with: pip install bleak")
        return 1
    
    print()
    
    # Scan for devices
    devices = await scan_devices()
    
    devices = list(filter(lambda x: "X6h" in x.name, devices))
    
    # Let user select device
    device = select_device(devices)
    if not device:
        print("No device selected. Exiting.")
        return 0
    
    print(f"\nSelected: {device.name or device.address}")
    
    # Ask for confirmation
    confirm = input("\nSend test print? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return 0
    
    # Send test print
    await test_print(device)
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
