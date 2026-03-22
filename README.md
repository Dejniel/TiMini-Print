# TiMini Print Bluetooth Printer Tool
Alternative [desktop software for Chinese Bluetooth thermal printers](https://github.com/Dejniel/TiMini-Print/releases) that use proprietary protocols (not ESC/POS), as a replacement for apps like “Tiny Print”, “Fun Print”, “Phomemo”, or “iBleem”.
It supports almost all mini printers! Check the huge list of [supported Bluetooth printer models](#supported-printer-models), or report missing ones.
It lets you print images, PDFs, or plain text from your computer. It supports both a GUI and a “fire-and-forget” CLI mode.

These printers are often sold on AliExpress and under generic names such as “thermal printer”, “mini printer”, or “cat printer”.
TiMini Print works on Windows, Linux, and macOS as a standalone tool without a system printer driver (it does not emulate a driver or print spooler)

## Motivation
I bought a Chinese mini printer and could not find any decent desktop software that met my expectations, so I wrote my own. This is also the kind of work I do professionally. If you need help with a similar problem, you can [contact me](https://inajiffy.eu/) — I can also help with [broader support or custom implementation](#looking-for-broader-support-or-implementation).

![TiMini Print LOGO EMX-040256 Printer Psi Patrol](EMX_040256.jpg)

# We need you!
- This project is open source! Your small monthly support on [Buy Me a Coffee](https://buymeacoffee.com/dejniel) can make a real difference and help keep it going—even a one-time donation helps. Building and maintaining a project like this takes a lot of time; if you find it useful, please consider supporting it so I can keep improving it: [support the project](https://buymeacoffee.com/dejniel)
- If you're a developer, contributions and bug reports are always welcome—please jump in. Especially if you use or build on non-Linux systems, please consider contributing fixes or improvements

## Looking for broader support or implementation?
- If you need reverse engineering (I mean security, obviously), broader commercial support, or a custom implementation, feel free to [reach out](https://inajiffy.eu/). I work on broken systems, neglected integrations, and projects that are already end-of-life, unsupported — or simply unsupportable. I also take on implementations that other teams are unwilling to touch.

# Requirements
You can find the latest standalone executable files on the [releases page](https://github.com/Dejniel/TiMini-Print/releases) and choose the asset that starts with `TiMini-Print-GUI-...` or `TiMini-Print-Command-Line-...` for your platform, or you can build the project yourself.

Theoretically, I support Windows, macOS, and Linux, but I test builds only on Ubuntu-like systems—if you need to run this elsewhere, please report issues or submit a fix :P

## Manual building requirements
- Python 3.8+
- `pip install -r requirements.txt` (Note: Windows + Python 3.13+: installing `winsdk` may require building binaries during download)
- (optional, GUI only) if `tkinter` is missing, install it from your system packages:
  Linux (Ubuntu/Debian): `sudo apt install python3-tk`
  macOS (Homebrew Python): `brew install python-tk`

# Quick start
If you use release binaries, run the downloaded executable directly.
If you build or run from source instead, use `python3 timiniprint_gui.py` or `python3 timiniprint_command_line.py`.

## Graphical user interface
You can scan, connect or disconnect with one button, choose a file, and print.
Start the graphical app by running the [downloaded executable file](#requirements).
On Linux and macOS, make sure it has execute permission first.

```bash
# Replace the filename below with the matching asset for your platform
chmod +x ./TiMini-Print-GUI-Linux-x86_64
./TiMini-Print-GUI-Linux-x86_64
```

Or run it from source:

```bash
python3 timiniprint_gui.py
```

## Command line interface
(the examples use Linux filenames)
- Print to the first supported Bluetooth printer:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 /path/to/file.pdf
  ```

- Print to a specific Bluetooth printer:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --bluetooth "PRINTER_NAME" /path/to/file.pdf
  ```

- Print via a serial port (skip Bluetooth connection):
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --serial /dev/rfcomm0 --model A200 /path/to/file.pdf
  ```

- Print raw text without creating a file:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --text "Hello from CLI"
  ```

- List available printer models:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --list-models
  ```

- Scan for supported printers:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --scan
  ```

## Notes
- If `--bluetooth` is omitted, the first supported printer found is used
- For `--serial`, you must pass `--model` (see `--list-models`)
- For Bluetooth printing, you can pass `--model` to override auto-detection

# Notes
- On first Classic connection on Windows/macOS, the system may request pairing confirmation
- Protocol integration guide: [docs/protocol.md](docs/protocol.md)

# Supported formats
- Images: .png .jpg .jpeg .gif .bmp
- PDF: prints all pages
- Text: .txt (monospace bold, word-wrapped by default)

# Supported printer models
A200, A33, A41II, A41III, A42II, A43, A4300, CMT-0510, CP01, D1, D100, DL GE225, DL X2, DL X2 Pro,
DL X7, DL X7 Pro, DT1-0, DTR-R0, DY03, DY49, EMX-040256, FC02, GB02SH, GB03PH, GB03PL, GB03SH,
GB03SL, GL-VS9, GT09, GT10, GV-MA211, GW08, GW09, HD1, HT0125, IM.04, IprintIt Printer,
JRX01, KF-5, LGM01, LP6, Label Printer CPLM10, Luxorp.PX10, ML-MP-01, MPA81, MV-B530, Mini Printer
CTP500, P2, P4, P5, P6, P7H, PT001, Pocket Printer, Professional Printer CTP100LG, QDID, QDX01,
ROSSMANN, RS9000, SC03H, Seznik Echo, Seznik Neo, Shipping Printer CTP800BD, Shipping Printer
CTP750BY, TCM690464, U1, UXPORTMIP, WL01, X103H, X103h, X16, XC9, YK06, YT01, ZHHC, ZP801, ZP802, ZPA4Z1,
0019B-C, 0019B-D, 15P3, 58P5, AI01, AN01, DY01, Ewtto ET-Z0504, FL01, GB01, GB02, GB03, GB04, GB05,
GB06, GT01, GT02, GT03, GT04, GT08, JX001 JX01, JX002 JX02, JX003 JX03, JX004 JX04, JX005 JX05,
JX006 JX06, JXM800 GG-D2100, LP100 LY10, LT01, LY01, LY02, LY03,
LY05, LY10, LY11, M01, M2, MX05, MX06, MX08, MX09, MX10, MX11, P1, P10, P5AI, P7, PR02, PR07, PR30, PR35, PR88, PR89, PR893, RT034h,
S01, S101, S102, XiaoWa, SC03, SC03h, SC04, SC04h, SC05, wts07, X1, X100, X101H, X102, X2H, X2h, X5,
X5H, X5HP, X5h, X6, X6H, X6HP, X6h, X7, X7H, X7HP, X7h, X8, X8-L, X8-W, X9, XW001 PR20, XW002 PR30,
XW003 PR25, XW004 PR35, XW005 PR88, XW006 PR89, XW007 PR893, XW008 PR02, XW009 PR07,
M08F, TP81, M832, Q302, T02

Alias-based Bluetooth name prefixes:
- mapped to GT01- defaults: YT02, MX01, MX02, MX03, MX07, MINIPRINTER, JL-BR22, URBANWORXKIDSCAMERA,
  CYLOBTPRINTER, MXTP-100, AZ-P2108X, MX12, PD01, XOPOPPY, MX13, BQ01, BQ02, EWTTOET-Z0499, BQ05, BQ06,
  BQ07, BQ7A, BQ7B, BQ08, BQ95, BQ96, MXW009, MXW010, EWTTOET-N3689, EWTTOET-N3687, MXPC-100, KP-IM606,
  BQ95B, BQ03, BQ95C, BQ06B, K06, BQ17
- mapped to TP81- defaults: TP84, TP85, TP86, TP87, TP88
- mapped to M832- defaults: M836
- mapped to Q302- defaults: Q580
- mapped to T02- defaults: T02E, Q02E, C02E

## Experimental support
These entries are available, but they still need more real-device reports and tuning. In Bluetooth device lists they appear as `[experimental]`.

Experimental printer models:
P100, MP100, P100S, MP100S, LP100S, P3, P3S

Experimental alias-based Bluetooth name prefixes:
- mapped to P100- defaults: YINTIBAO-V5, MP200, MP220, AEQ918N4
- mapped to P100S- defaults: YINTIBAO-V5PRO, MP200S, MP220S
- mapped to LP100 / LP100S defaults: LP220, LY100_BLE, LP220S
- mapped to P3 / P3S defaults: MP300, MP300S
- mapped to V5X- defaults: MXW01, MXW01-1, X1, X2, C17, MXW-W5, AC695X_PRINT
- mapped to V5X- defaults: JK01, PORTABLEPRINTER, INSTANTPRINTPLUS, REKA, HDMDT-00, KERUI, BH03
- mapped to GT01- defaults: YTB01
- mapped to D1- defaults: C21, D2, E2, NEWSMY
- mapped to M08F- defaults: MXW-A4
