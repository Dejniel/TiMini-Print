# TiMini Print Bluetooth Printer Tool
Alternative [desktop software for Chinese Bluetooth thermal printers](https://github.com/Dejniel/TiMini-Print/releases) that use proprietary protocols (not ESC/POS), as a replacement for apps like “Tiny Print”, “Fun Print”, “Phomemo”, “Luck Jingle”, or “iBleem”.
It supports almost all mini printers! Check the huge list of [supported Bluetooth printer models](#supported-printer-models), or report missing ones.
It lets you print images, PDFs, or plain text from your computer. It supports both a GUI and a “fire-and-forget” CLI mode, plus [custom integrations](#library-integration).

These printers are often sold on AliExpress and under generic names such as “thermal printer”, “mini printer”, or “cat printer”.
TiMini Print works on Windows, Linux, and macOS as a standalone tool without a system printer driver (it does not emulate a driver or print spooler)

## Motivation
I bought a Chinese mini printer and could not find any decent desktop software that met my expectations, so I wrote my own. This is also the kind of work I do professionally. If you need help with a similar problem, you can [contact me](https://inajiffy.eu/) — I can also help with [broader support or custom implementation](#looking-for-broader-support-or-implementation)

![TiMini Print LOGO EMX-040256 Printer Psi Patrol](EMX_040256.jpg)

# We need you!
- This project is open source! Your small monthly support on [Buy Me a Coffee](https://buymeacoffee.com/dejniel) can make a real difference and help keep it going—even a one-time donation helps. Building and maintaining a project like this takes a lot of time; if you find it useful, please consider supporting it so I can keep improving it: [support the project](https://buymeacoffee.com/dejniel)
- If you're a developer, contributions and bug reports are always welcome—please jump in. Especially if you use or build on non-Linux systems, please consider contributing fixes or improvements

## Looking for broader support or implementation?
- If you need security/reverse engineering, broader commercial support, or a custom implementation, feel free to [reach out](https://inajiffy.eu/). I work on broken systems, neglected integrations, and projects that are already end-of-life, unsupported — or simply unsupportable. I also handle custom implementation work that sits outside the usual support model

# Requirements
You can find the latest standalone executable files on the [releases page](https://github.com/Dejniel/TiMini-Print/releases) and choose the asset that starts with `TiMini-Print-GUI-...` or `TiMini-Print-Command-Line-...` for your platform, or you can build the project yourself

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
On Linux, make sure it has execute permission first.

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
  ./TiMini-Print-Command-Line-Linux-x86_64 --bluetooth "PRINTER_NAME" --export-device-config printer.json
  ./TiMini-Print-Command-Line-Linux-x86_64 --serial /dev/rfcomm0 --device-config printer.json /path/to/file.pdf
  ```

- Print raw text without creating a file:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --text "Hello from CLI"
  ```

- List available printer profiles:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --list-profiles
  ```

- Scan for supported printers:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --scan
  ```

## Notes
- If `--bluetooth` is omitted, the first supported printer found is used
- For `--serial`, you must pass `--device-config`
- `--export-device-config` writes the full resolved runtime config as JSON and `--device-config` loads that JSON back and forces the saved protocol/profile/runtime values

# Notes
- On first Classic connection on Windows/macOS, the system may request pairing confirmation

## Library integration
If you want to build your own integration instead of using only the bundled GUI or CLI, start with [docs/protocol.md](docs/protocol.md). It is the practical first-steps guide to creating a `PrinterDevice`, building a printable job, and sending it through a connector from your own code. If you also want the package boundaries and design rationale behind that API, continue with [docs/architecture.md](docs/architecture.md).

# Supported formats
- Images: .png .jpg .jpeg .gif .bmp
- PDF: prints all pages
- Text: .txt (monospace bold, word-wrapped by default)

# Supported printer models
A2, A2H, A40, A49, A80H-HD, A200, A33, A41II, A41III, A42II, A43, A4300, APL86, APL86H, APA40, APA41, APA42, APA43, APA46Y, APA49H, CMT-0510, CP01, D1, D80, D100, DL GE225, DL X2, DL X2 Pro, DL X7, DL X7 Pro, DP_A4, DP_8038, DT1-0, DTR-R0, DY03, DY49, DYA46, DYA49, DYD80, DYD80H, EMX-040256, FC02, GB01, GB02, GB02SH, GB03, GB03PH, GB03PL, GB03SH, GB03SL, GB04, GB05, GB06, GL-VS9, GT01, GT03, GT04, GT08, GT09, GT10, GW08, GW09, HD1, HT0125, IM.04, IprintIt Printer, ITP05, ITP05H, ITP06, JRX01, JX001, JX002, JX003, JX004, JX005, JX006, JXM800, KF-5, L86, L86H_Printer, L86_Printer, LGM01, LP6, LP100, Label Printer CPLM10, LuckP_A41, LuckP_A42, Luxorp.PX10, ML-MP-01, MPA81, MV-B530, Mini Printer CTP500, MX05, MX07, MX08, MX09, MX11, P1, P2, P4, P5, P5AI, P6, P7, P7H, P10, PR02, PR07, PR20, PR25, PR30, PR35, PR88, PR89, PR893, PT001, PeriPage_A40, Pocket Printer, Professional Printer CTP100LG, QDID, QDX01, QIRUI_Q1, QIRUI_Q2, ROSSMANN, RS9000, S01, S101, S102, SC03, SC03H, SC03h, SC04, SC04H, SC04h, SC05, Seznik Echo, Seznik Neo, Shipping Printer CTP750BY, Shipping Printer CTP800BD, TCM690464, TPA46, TPA46Pro, U1, U8, UXPORTMIP, V5X, WL01, WTS07, X100, X101H, X102, X103H, X103h, X16, X2H, X2h, X5, X5H, X5HP, X5h, X6H, X6HP, X6h, X7, X7H, X7HP, X7h, X8, X8-L, X8-W, X9, XC9, XiaoWa, XOPOPPY, YK06, YTB01, ZHHC, ZP801, ZP802, ZPA4Z1, 0019B-C, 0019B-D, 15P3, 58P5, AN01, DY01, Ewtto ET-Z0504, FL01, LT01, LY01, LY02, LY03, LY05, LY11, M01, M2, RT034h
- A2 and clones: PPA2, PPA2_, A2_EY48D, A2_LYiN48D_ITSR
- A2H and clones: PPA2H, PPA2H_, A2_LYiN48DH
- A40 and clones: A40_
- A49 and Lujiang A4 clones: APA49, APA49_
- APA49H and clones: APA49H_
- A80H-HD and clones: DP_A80H_
- APA40 / APA41 / APA42 / APA43 Lujiang A4 variants and clones: APA40_, APA41_, APA42_, APA43_
- APA46Y and clones: APA46Y_
- APL86 and clones: APL86_, L86, L86_Printer
- APL86H and clones: APL86H_, APL86HL, L86H_Printer
- D80 and clones: DP_D80_, DP-D80_, D80_, D80-, E80_, CASA-01_, PeriPage_A40, DYD80
- DYD80H and clones: DP_D80H_
- DP_A4 and clones: DP_A4_, DP-A4_
- DP_8038 and clones: DP_8038_
- ITP05 and clones: DP_ITP05_, ITP05_, ITP05H, DYA46
- ITP06 and clones: DP_ITP06_, ITP06_, DYA49
- QIRUI_Q1 and clones: QIRUI_Q1_
- QIRUI_Q2 and clones: QIRUI_Q2_
- LuckP_A41 and clones: LuckP_A41_
- LuckP_A42 and clones: LuckP_A42_
- TPA46 and clones: TPA46_
- TPA46Pro and clones: TPA46Pro_
- U8 and clones: U8_
- JX001 and clones: JX01
- JX002 and clones: JX02
- JX003 and clones: JX03
- JX004 and clones: JX04
- JX005 and clones: JX05
- JX006 and clones: JX06
- JXM800 and clones: GG-D2100
- LP100 and clones: LY10
- MX02 and clones: MX03
- MX06 and clones: MXTP-100, CYLO BT PRINTER, EWTTO ET-Z0499
- MX10 and clones: AZ-P2108X, PD01, MX12, MX13, MXW009, MXW010, KP-IM606, GV-MA211
- BQ02 and clones: BQ03, BQ17
- GT02 and clones: MINI PRINTER, JL-BR22
- YT01 and clones: YT02, MX01, MXPC-100, URBANWORX KIDS CAMERA, BQ01, BQ05, BQ06, BQ06B, BQ07, BQ08, BQ7A, BQ7B, BQ95, BQ95B, BQ95C, BQ96, EWTTO ET-N3687, EWTTO ET-N3689, K06, X6
- V5X and clones: AI01, MXW01, MXW01-1, MXW-W5, X1, X2, C17, AC695X_PRINT, JK01, PORTABLEPRINTER, INSTANTPRINTPLUS, REKA, HDMDT-00, KERUI, BH03
- M08F and clones: TP81, TP84, TP85, TP86, TP87, TP88, M832, M836, Q302, Q580, T02, T02E, Q02E, C02E
- PR20 and clones: XW001
- PR25 and clones: XW003
- PR30 and clones: XW002
- PR35 and clones: XW004
- PR88 and clones: XW005
- PR89 and clones: XW006
- PR893 and clones: XW007
- PR02 and clones: XW008
- PR07 and clones: XW009

## Experimental support
These entries are available, but they still need more real-device reports and tuning. In Bluetooth device lists they appear as `[experimental]`

Experimental printer models:
P100, MP100, P100S, MP100S, LP100S, P3, P3S

Experimental Bluetooth names:
- P100 and clones: YINTIBAO-V5, MP200, MP220, AEQ918N4
- P100S and clones: YINTIBAO-V5PRO, MP200S, MP220S
- LP100 and clones: LP220, LY100_BLE
- LP100S and clones: LP220S
- P3 and clones: MP300
- P3S and clones: MP300S
- DCK and clones: C21, D2, E2, NEWSMY
- V10G and clones: MXW-A4
