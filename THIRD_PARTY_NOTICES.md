# Third-party notices

TiMini-Print release builds use the following directly declared components:

| Component | Version | License |
| --- | --- | --- |
| Pillow | 12.3.0 | MIT-CMU |
| pypdfium2 and PDFium | 5.11.0 | BSD-3-Clause, Apache-2.0, and build-specific dependency licenses |
| Bleak | 3.0.2 | MIT |
| crc8 | 0.2.1 | MIT |
| pySerial | 3.5 | BSD-3-Clause |
| platformdirs | 4.10.0 | MIT |
| PyObjC IOBluetooth | 12.2.1 | MIT |
| winsdk | 1.0.0b10 | MIT |
| PyInstaller | 6.21.0 | GPL-2.0-or-later with the PyInstaller bootloader exception |

Each binary archive contains license files for the exact package versions used
on that platform under `licenses/python/`. The pySerial 3.5 text comes from its
official release because its wheel omits the file. The archive also includes
pypdfium2's platform-specific `BUILD_LICENSES` directory for PDFium and its
bundled dependencies. `THIRD_PARTY_MANIFEST.txt` records the exact installed
versions, including active transitive dependencies.

The presence of a notice does not imply endorsement by a component's authors.
