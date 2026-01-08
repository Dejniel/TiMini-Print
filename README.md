EMX-040256 Printer Tool (Linux, /dev/rfcomm0)

This is a small Python project for printing images and text to the EMX-040256
thermal printer over a serial Bluetooth SPP device (for example: /dev/rfcomm0).

![EMX-040256 Printer Psi Patrol](EMX_040256.jpg)

Requirements
- Python 3.8+
- pip install -r requirements.txt

Quick start
- Print an image:
  python3 print_emx_040256.py /path/to/photo.png
- Print text:
  python3 print_emx_040256.py /path/to/text.txt

