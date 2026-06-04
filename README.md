# TiMini Print Bluetooth Printer Tool
Alternative [desktop software for Chinese Bluetooth thermal printers](https://github.com/Dejniel/TiMini-Print/releases) that use proprietary protocols (not ESC/POS), as a replacement for apps like “Tiny Print”, “Fun Print”, “Phomemo”, “Luck Jingle”, “NIIMBOT”, or “iBleem”.
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
  ./TiMini-Print-Command-Line-Linux-x86_64 --export-config luck_a2 printer.json
  ./TiMini-Print-Command-Line-Linux-x86_64 --serial /dev/rfcomm0 --config printer.json /path/to/file.pdf
  ```

- Force a specific model for an unsupported or random Bluetooth name:
  ```bash
  ./TiMini-Print-Command-Line-Linux-x86_64 --list-profiles
  ./TiMini-Print-Command-Line-Linux-x86_64 --export-config luck_a2 printer.json
  ./TiMini-Print-Command-Line-Linux-x86_64 --config printer.json /path/to/file.pdf
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
- If `--bluetooth` and `--config` are omitted, the first supported printer found is used
- With `--config`, `--bluetooth` is optional; without it, the first Bluetooth target found is used unless the config has a saved Bluetooth target
- Use `--bluetooth NAME_OR_ADDRESS --config printer.json` when several Bluetooth devices are nearby and you want to choose one explicitly
- For `--serial`, you must pass `--config`
- `--config KEY` uses a known profile/runtime defaults key directly; `--config PATH` loads an editable JSON config
- `--export-config KEY PATH` writes a full editable JSON config from a known profile/runtime defaults key
- Manual config overrides are for advanced testing only; if you force the wrong profile or protocol family, printing may still fail
- `--paper-mode tag` or `--paper-mode plain` overrides the profile's default media mode when the selected protocol supports it

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
0019B-C, 0019B-D, 15P3, 58P5, A200, A33, A40, A41II, A41III, A42II, A43, A4300, AI01, AN01, APA40, APA41, APA42, APA43, APA46Y, APA49H, CMT-0510, CP01, CPLM10 (Label Printer), CTP100LG (Professional Printer), CTP500 (Mini Printer), CTP750BY (Shipping Printer), CTP800BD (Shipping Printer), D1, D100, D110, DL_GE225, DL_X2, DL_X2Pro, DL_X7, DL_X7Pro, DP_8038, DP_A4, DT1-0, DTR-R0, DY01, DY03, DY49, EMX-040256, ewtto ET-Z0504, FC02, FL01, GB01, GB02, GB02SH, GB03, GB03PH, GB03PL, GB03SH, GB03SL, GB04, GB05, GB06, GG-D2100 (JXM800), GL-VS9, GT01, GT03, GT04, GT09, GT10, GW08, GW09, HD1, HT0125, IM.04, IprintIt Printer, JRX01, JX01 (JX001), JX02 (JX002), JX03 (JX003), JX04 (JX004), JX05 (JX005), JX06 (JX006), KF-5, LGM01, LP6, LT01, LuckP_A41, LuckP_A42, Luxorp.PX10, LY01, LY02, LY03, LY05, LY11, M2, ML-MP-01, MPA81, MV-B530, MX02, MX03, MX07, MX08, MX09, MX11, MX12, MX13, MXW010, P1 (legacy), P1 (TSPL), P10, P2, P4, P5, P5AI, P6, P7, P7H, PD01, Pocket Printer, PPA2L, PPA2LH, PR07, PT001, QDID, QDX01, QIRUI_Q1, QIRUI_Q2, ROSSMANN, RS9000, RT034h, S01, S101, S102, SC03, SC03H, SC03h, SC04, SC04h, SC05, SeznikEcho, SeznikNeo, TCM690464, TPA46, TPA46Pro, U1, U8, UXPORTMIP, WL01, wts07, X100, X101H, X102, X103H, X103h, X16, X2H, X2h, X5, X5H, X5h, X5HP, X6H, X6h, X6HP, X7, X7H, X7h, X7HP, X8, X8-L, X8-W, X9, XC9, XiaoWa, XOPOPPY, YK06, YTB01, ZHHC, ZP801, ZP802, ZPA4Z1
- A2 and clones: PPA2, A2_EY48D, A2_LYiN48D_ITSR
- A2H and clones: PPA2H, A2_LYiN48DH
- A49 and clones: APA49
- A80H-HD and clones: DP_A80H
- APL86 and clones: L86, L86_Printer
- APL86H and clones: APL86HL, L86H_Printer
- BQ02 and clones: BQ03, BQ17
- D80 and clones: DYD80, PeriPage_A40, DP_D80, E80, CASA-01
- DYD80H and clones: DP_D80H
- GT02 and clones: MINI PRINTER, JL-BR22
- GT08 and clones: PR88, XW005
- ITP05 and clones: ITP05H, DYA46, DP_ITP05
- ITP06 and clones: DYA49, DP_ITP06
- M01 and clones: PR25, XW003, XW009
- M02 (Phomemo) and clones: M02S
- MX06 and clones: MX05
- MX10 and clones: AZ-P2108X, MXW009, KP-IM606, GV-MA211
- MXTP-100 and clones: CYLO BT PRINTER, EWTTO ET-Z0499
- P11 (HPRT ESC) and clones: P2_, P3_, P5_, YHK_
- PR02 and clones: XW008
- PR20 and clones: XW001
- PR30 and clones: XW002
- PR35 and clones: XW004
- PR89 and clones: XW006
- PR893 and clones: XW007
- T02 (Phomemo) and clones: T02E, Q02E, C02E
- V5X and clones: MXW01, MXW01-1, MXW-W5, X1, X2, C17, AC695X_PRINT, JK01, PORTABLEPRINTER, INSTANTPRINTPLUS, REKA, HDMDT-00, KERUI, BH03
- YT01 and clones: YT02, MX01, MXPC-100, URBANWORX KIDS CAMERA, BQ01, BQ05, BQ06, BQ06B, BQ07, BQ08, BQ7A, BQ7B, BQ95, BQ95B, BQ95C, BQ96, EWTTO ET-N3687, EWTTO ET-N3689, K06, X6
<!-- END supported-models -->

## Potential future support
These models or protocol families are not in the supported list yet, but they look implementable with [more support](#we-need-you).
<!-- BEGIN todo-models -->
B1, B1 Pro, B18, B18S, B21, B21-C2B, B21-L2B, B21S, B21S-C2B, B21_Pro, B24, B3S, B3S_P, D101, D11, D11S, D11_H, D110_M, Hi-D110, Hi-NB-D11, JCB3S, M2_H, N1, S6, JX400R, JX400R06P, MP300, MXW-A4
- AL200 and clones: AL2, RPP02N
- BAYPAGE and clones: YINTIBAO-V8S
- C21 and clones: D2, E2, NEWSMY
- D12 and clones: D11s, C2, C3, C16
- D400 and clones: Y810BT, QR380A, TB41, QR_386A, ITPP941, P80S, ITPP130B
- D82 and clones: D82S, D83, A10, FICHERO_6181
- ITP05N and clones: ITP06N, PCPS_D80, DP_A80, DP_A80S, DP_A80W, PD_A4, GD-88
- JXPRINTER and clones: PRINTER
- LP100 and clones: LP220, LY100_BLE
- LP100S and clones: LP220S
- M08F and clones: TP81, TP84, TP85, TP86, TP87, TP88
- M832 and clones: M836
- P100 and clones: MP100, MP200, MP220, YINTIBAO-V5, AEQ918N4
- P100S and clones: MP100S, MP200S, MP220S, YINTIBAO-V5PRO
- P3S and clones: MP300S
- Q302 and clones: Q580
- YINTIBAO and clones: PAPERGO
<!-- END todo-models -->
