# Third-party notices

TiMini-Print generates one platform-specific license document from the dependencies installed in the current environment. Release executables embed this document at build time, while source runs generate it on demand. It contains an exact component manifest followed by the project license and complete third-party license texts, including pypdfium2's `BUILD_LICENSES` content for PDFium and its bundled dependencies.

Where an installed wheel omits its license file, the text is taken from the matching official release stored in `licenses/`; this currently applies to pySerial, PyObjC framework wheels, PyWinRT wheels, and winsdk. Run the executable with `--licenses`, or use the `Licenses` link in the GUI, to display the complete document.

The presence of a notice does not imply endorsement by a component's authors.
