# Contributing

Thanks for helping TiMini-Print support more printers and work better across platforms. Keep each pull request focused on one change, explain what it fixes, and include the smallest useful tests.

## Before sending a change

- Run the smallest relevant test set and include the exact command in the pull request.
- Add a regression test when fixing a bug.
- Say clearly whether you tested only the code or also used a physical printer.
- For printer tests, include the model, detected Bluetooth name, transport, relevant profile/runtime settings, and the exact before/after result.
- Do not assume that one tested model proves the same behavior for a whole printer family.

## Rights and source material

Before a pull request can be merged, each contributor must accept the [TiMini-Print Contributor License Agreement](CLA.md) through [CLA Assistant](https://cla-assistant.io/Dejniel/TiMini-Print). You keep ownership of your work. The agreement lets the project publish it under Apache-2.0 and also use, sublicense, or relicense it in commercial and proprietary products. The exact agreement version and your acceptance are recorded by CLA Assistant. The signing copy of [version 1.0](https://gist.github.com/Dejniel/e13064e7a6c68ae83c8e6560cfcf4039/2aa4e00f94b195a9955654da7c130bf30699e2fb) is preserved on GitHub.

Do not submit code, assets, data, credentials, personal data, unique device identifiers, or other material copied from manufacturer applications, firmware, SDKs, or other proprietary sources. Do not attach proprietary binaries, decompiled sources, private source dumps, or packet captures containing identifiers.

Protocol changes must be your own implementation based on lawful observations, public documentation, or test data you own. Briefly describe how you learned the protocol behavior and how you tested it. Remove Bluetooth addresses, serial numbers, credentials, and other device-specific identifiers from logs and fixtures.

If you created the contribution for an employer or another organization, make sure you have permission to submit it.

CLA signing data is handled as described in the [CLA privacy notice](CLA_PRIVACY.md). If CLA Assistant is temporarily unavailable, the pull request must wait until acceptance can be recorded; a checked box in the pull request is not a substitute for the signing record.

For the project's architecture and review rules, see `AGENTS.md`.
