# Contributing

Thanks for helping TiMini-Print support more printers and work better across platforms. Keep each pull request focused on one change, explain what it fixes, and include the smallest useful tests.

## Before sending a change

- Run the smallest relevant test set and include the exact command in the pull request.
- Add a regression test when fixing a bug.
- Say clearly whether you tested only the code or also used a physical printer.
- For printer tests, include the model, detected Bluetooth name, transport, relevant profile/runtime settings, and the exact before/after result.
- Do not assume that one tested model proves the same behavior for a whole printer family.

## Rights and source material

By submitting a contribution, you confirm that you have the right to submit it and agree that it is licensed under Apache-2.0.

Do not submit code, assets, data, credentials, personal data, unique device identifiers, or other material copied from manufacturer applications, firmware, SDKs, or other proprietary sources. Do not attach proprietary binaries, decompiled sources, private source dumps, or packet captures containing identifiers.

Protocol changes must be your own implementation based on lawful observations, public documentation, or test data you own. Briefly describe how you learned the protocol behavior and how you tested it. Remove Bluetooth addresses, serial numbers, credentials, and other device-specific identifiers from logs and fixtures.

If you created the contribution for an employer or another organization, make sure you have permission to submit it.

## Contributor license agreement

Most contributions are accepted under Apache-2.0 without any additional agreement. For a substantial contribution, the maintainer may apply the `cla required` label and ask the contributor to sign the [TiMini-Print Contributor License Agreement](CLA.md) before merge.

When requested, complete and sign the [version 1.0 copy](https://gist.github.com/Dejniel/e13064e7a6c68ae83c8e6560cfcf4039/8232fef33dced59e041e55814b1bac7de9d1062e), then email the signed PDF or scan to `cla@wtrymiga.pl`. Signing data is handled as described in the [CLA privacy notice](CLA_PRIVACY.md).

For the project's architecture and review rules, see `AGENTS.md`.
