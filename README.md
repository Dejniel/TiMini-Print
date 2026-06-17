# TiMini Print Bluetooth Printer Tool
Alternative [desktop software for Chinese Bluetooth thermal printers](https://github.com/Dejniel/TiMini-Print/releases) that use proprietary protocols (not ESC/POS), as a replacement for apps like “Tiny Print”, “Fun Print”, “Phomemo”, “Luck Jingle”, “NIIMBOT”, “iBleem”, or “Eleph-label”.
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
- `pip install -r requirements.txt`
  - Windows + Python 3.13+: installing `winsdk` may require building binaries during download
  - If `python-lzo` needs a source build, install the LZO development package first:
    Linux (Ubuntu/Debian): `sudo apt install liblzo2-dev`
    macOS (Homebrew Python): `brew install lzo`
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
  ./TiMini-Print-Command-Line-Linux-x86_64 --export-printer-config luck_a2 printer.json
  ./TiMini-Print-Command-Line-Linux-x86_64 --serial /dev/rfcomm0 --printer-config printer.json /path/to/file.pdf
  ```

- Force a specific model for an unsupported or random Bluetooth name:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --list-models
  ./TiMini-Print-Command-Line-Linux-x86_64 --export-printer-config luck_a2 printer.json
  ./TiMini-Print-Command-Line-Linux-x86_64 --printer-config printer.json /path/to/file.pdf
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
- If `--bluetooth` and `--printer-config` are omitted, the first supported printer found is used
- With `--printer-config`, `--bluetooth` is optional; without it, the first Bluetooth target found is used unless the printer config has a saved Bluetooth target
- Use `--bluetooth NAME_OR_ADDRESS --printer-config printer.json` when several Bluetooth devices are nearby and you want to choose one explicitly
- For `--serial`, you must pass `--printer-config`
- `--printer-config KEY` uses a known model key or public model name directly; `--printer-config PATH` loads an editable printer config JSON
- `--export-printer-config KEY PATH` writes a full editable printer config JSON from a known model key or public model name
- Manual printer config overrides are for advanced testing only; if you force the wrong profile or protocol family, printing may still fail
- `--paper-mode tag` or `--paper-mode plain` overrides the profile's default media mode when the selected protocol supports it
- The GUI and standalone CLI release builds check GitHub releases at startup at most once per day; set `TIMINIPRINT_NO_UPDATE_CHECK=1` to disable this

# Notes
- On first Classic connection on Windows/macOS, the system may request pairing confirmation

## Library integration
If you want to build your own integration instead of using only the bundled GUI or CLI, start with [docs/protocol.md](docs/protocol.md). It is the practical first-steps guide to creating a `PrinterDevice`, building a printable job, and sending it through a connector from your own code. If you also want the package boundaries and design rationale behind that API, continue with [docs/architecture.md](docs/architecture.md).

# Supported formats
- Images: .png .jpg .jpeg .gif .bmp
- PDF: prints all pages
- Text: .txt (monospace bold, word-wrapped by default)

# Supported printer models
<!-- BEGIN supported-models -->
A33, A40, A41III, A43, A4300, AI01, AN01, APA46Y, APA49H, CPLM10, CTP100LG, CTP500, D100, D110, DL_X2, DL_X7, DL_X7Pro, GB01, GB02, GB02SH, GB03SL, GB04, GB05, GG-D2100, GT02, GT04, GW09, HD1, LT01, LuckP_A41, LuckP_A42, LY01, LY02, LY03, LY05, LY10, LY11, M02X, M2, M220, MPA81, MX02, MX03, MX07, MX08, MX09, MXW010, P1, P10, P4, P6, P7, PD01, PPA2L, PPA2LH, PR30, PT001, QIRUI_Q1, QIRUI_Q2, S01, S101, S102, U1, U8, X1, X101H, X16, X5, X6, X7, X8, XW002, YTB01, ZHHC, ZP802

- 15P3 and clones: YK06
- 58P5 and clones: WL01
- A41II and clones: A42II, A200
- A80H-HD and clones: DP_A80H
- APA40 and clones: APA42, APA43
- APA41 and clones: APA49, A49
- APL86 and clones: L86, L86_Printer
- APL86H and clones: APL86HL, L86H_Printer
- BQ02 and clones: BQ03, BQ17
- C21 and clones: D2, E2, NEWSMY
- CMT-0510 and clones: SC03, SC04
- CTP750BY and clones: CTP800BD
- D80 and clones: PeriPage_A40, DYD80, DP_D80, DP-D80, E80, CASA-01
- DL_X2Pro and clones: P5
- DYD80H and clones: DP_D80H
- ewtto ET-Z0504 and clones: IM.04, X103H, SC05, X102, X5HP, X6HP, P7H, X2H, X5H, X6H, X7H
- FL01 and clones: KF-5
- GB03 and clones: GB06
- GB03PH and clones: GB03SH
- GT01 and clones: GT03
- GT08 and clones: GW08, XW005, PR88
- GT09 and clones: GT10
- IprintIt Printer and clones: DY01, LP6
- ITP05 and clones: ITP05H, DYA46, DP_ITP05, TPA46, DP_A4, DP-A4, DP_8038
- ITP06 and clones: DYA49, DP_ITP06, TPA46Pro
- JX01 and clones: JX02, JX03, JX04, JX05, JX06
- LGM01 and clones: X7HP
- M02 and clones: Mr.in, Mr.in_M02
- M02 Pro and clones: M02PRO
- M02S and clones: Mr.in_M02S
- M110 and clones: M120
- MINIPRINTER and clones: JL-BR22
- MV-B530 and clones: GL-VS9, QDID, X9
- MX05 and clones: MX06, MXTP-100, CYLOBTPRINTER, EWTTOET-Z0499
- MX10 and clones: AZ-P2108X, MXW009, KP-IM606, GV-MA211
- MX11 and clones: MX12
- MX13 and clones: XOPOPPY
- P11 and clones: P2, P3, P5, YHK
- P5AI and clones: PR07, PR25, XW003, XW009, M01
- Pocket Printer and clones: Luxorp.PX10, EMX-040256, SeznikEcho, TCM690464, UXPORTMIP, DL_GE225, ML-MP-01, ROSSMANN, 0019B-C, 0019B-D, DTR-R0, GB03PL, HT0125, RT034h, DT1-0, SC03h, SC04h, X103h, DY03, X100, X2h, X5h, X6h, X7h, XC9, D1, P1, P2
- PPA2 and clones: A2, A2_EY48D, A2_LYiN48D_ITSR
- PPA2H and clones: A2H, A2_LYiN48DH
- PR02 and clones: XW008
- PR20 and clones: XW001
- PR893 and clones: XW007
- SC03H and clones: FC02
- SeznikNeo and clones: RS9000, XiaoWa, JRX01, QDX01, wts07, CP01, DY49
- T02 and clones: T02E, Q02E, C02E
- V5X and clones: X1, X2, MXW01, MXW01-1, C17, MXW-W5, AC695X_PRINT, JK01, PORTABLEPRINTER, INSTANTPRINTPLUS, REKA, HDMDT-00, KERUI, BH03
- XW004 and clones: PR35
- YT01 and clones: YT02, MX01, MX05, MX06, MX08, MX09, MX10, MX11, MX12, MX13, MXTP-100, MXPC-100, AZ-P2108X, PD01, URBANWORXKIDSCAMERA, CYLOBTPRINTER, XOPOPPY, BQ01, EWTTOET-Z0499, BQ05, BQ95B, BQ95C, BQ95, BQ06B, BQ06, BQ07, BQ7A, BQ7B, BQ08, BQ96, MXW009, MXW010, EWTTOET-N3689, EWTTOET-N3687, KP-IM606, GV-MA211, X6, K06
- ZPA4Z1 and clones: ZP801, XW006, PR89, X8-L, X8-W
<!-- END supported-models -->

## Potential future support
These models or protocol families are not in the supported list yet, but they look implementable with [more support](#we-need-you).
<!-- BEGIN todo-models -->
D110_M, Hi-D110, JX400R, JX400R06P, MP300, MXW-A4

- AL200 and clones: RPP02N, AL2
- B1 and clones: B1 Pro, M2_H, N1
- B18 and clones: B18S
- B21 and clones: B21S-C2B, B21-C2B, B21-L2B, B21_Pro, B21S
- B3S and clones: B3S_P, JCB3S, S6_P, B24, S6
- BAYPAGE and clones: YINTIBAO-V8S
- D101 and clones: Betty
- D11 and clones: Hi-NB-D11, D11_Pro, D11_H, D11S, Fust, D61, D41, Dxx
- D12 and clones: C16, C2, C3
- D30 and clones: Q30S, D35, D50, Q30
- D400 and clones: ITPP130B, QR_386A, ITPP941, Y810BT, QR380A, TB41, P80S
- D82 and clones: FICHERO_6181, D82S, D83, A10
- ITP05N and clones: PCPS_D80, DP_A80S, DP_A80W, ITP06N, DP_A80, PD_A4, GD-88
- JXPRINTER and clones: PRINTER
- LP100 and clones: LY100_BLE, LP220
- LP100S and clones: LP220S
- M03 and clones: M200, M250, M221, M260
- M04S and clones: M04AS
- M08F and clones: TP81, TP84, TP85, TP86, TP87, TP88
- M832 and clones: M836
- MPL11 and clones: D11s, FICHERO_5836, MULLER_6473
- P100 and clones: YINTIBAO-V5, AEQ918N4, MP100, MP200, MP220
- P100S and clones: YINTIBAO-V5PRO, MP100S, MP200S, MP220S
- P12 and clones: P12 Pro, A30
- P3S and clones: MP300S
- PM-241-BT and clones: PM241, PM 241
- Q302 and clones: Q580
- YINTIBAO and clones: PAPERGO
<!-- END todo-models -->
