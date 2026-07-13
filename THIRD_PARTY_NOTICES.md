# Third-party notices

TiMini-Print release builds use the following directly declared components:

| Component | License |
| --- | --- |
| Pillow | MIT-CMU |
| pypdfium2 and PDFium | BSD-3-Clause, Apache-2.0, and build-specific dependency licenses |
| Bleak | MIT |
| crc8 | MIT |
| pySerial | BSD-3-Clause |
| platformdirs | MIT |
| PyObjC IOBluetooth | MIT |
| winsdk | MIT |
| PyInstaller | GPL-2.0-or-later with the PyInstaller bootloader exception |

Each release executable contains one platform-specific license document with the exact installed versions and their license texts. This includes pypdfium2's `BUILD_LICENSES` content for PDFium and its bundled dependencies. Where a wheel omits its license file, the text is taken from the matching official release; this currently applies to pySerial, PyObjC framework wheels, PyWinRT wheels, and winsdk. Run the executable with `--licenses`, or use the `Licenses` link in the GUI, to display the complete document.

The presence of a notice does not imply endorsement by a component's authors.
