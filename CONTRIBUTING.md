# Contributing

Thanks for helping TiMini-Print support more printers and work better across platforms. Keep each pull request focused on one change, explain what it fixes, and include the smallest useful tests.

## Before sending a change

- Run the smallest relevant test set and include the exact command in the pull request.
- Add a regression test when fixing a bug.
- Say clearly whether you tested only the code or also used a physical printer.
- For printer tests, include the model, detected Bluetooth name, transport, relevant profile/runtime settings, and the exact before/after result.
- Do not assume that one tested model proves the same behavior for a whole printer family.

## Rights and source material

By submitting a contribution, you confirm that you have the right to submit it and agree that it is licensed under Apache-2.0. Most contributions need nothing more.

Do not submit code, assets, data, credentials, personal data, unique device identifiers, or other material copied from manufacturer applications, firmware, SDKs, or other proprietary sources. Do not attach proprietary binaries, decompiled sources, private source dumps, or packet captures containing identifiers.

Protocol changes must be your own implementation based on lawful observations, public documentation, or test data you own. Briefly describe how you learned the protocol behavior and how you tested it. Remove Bluetooth addresses, serial numbers, credentials, and other device-specific identifiers from logs and fixtures.

If you created the contribution for an employer or another organization, make sure you have permission to submit it.

## When a CLA is required

The maintainer may require the [TiMini-Print Contributor License Agreement](CLA.md) before merging a contribution that would be costly to remove or independently reimplement in a future commercial product. This is an additional grant of rights; you keep ownership of your work and the public contribution remains available under Apache-2.0.

A CLA will normally be required when a contribution:

- adds a protocol family, codec, compression method, transport backend, rendering algorithm, or substantial runtime behavior;
- adds a new module or roughly 50 or more meaningful lines of original code or tests, excluding generated files;
- would take more than one or two hours to independently reimplement and verify;
- is needed to retain support for a printer or another significant feature;
- becomes substantial when combined with the contributor's earlier accepted work; or
- is submitted on behalf of an employer or may involve patent rights.

A CLA will normally not be required for typo and formatting fixes, small documentation changes, dependency or CI maintenance, factual catalog updates, diagnostic reports, or small and obvious fixes that can be independently recreated quickly. Line counts are only a review signal, not a legal test. The practical question is whether removing the contribution from a commercial version would create a real engineering cost.

The maintainer records the decision with a `license: apache` or `license: cla` pull-request label. When a CLA is required, complete the signature section in the preserved [version 1.0 signing copy](https://gist.github.com/Dejniel/e13064e7a6c68ae83c8e6560cfcf4039/8232fef33dced59e041e55814b1bac7de9d1062e), sign a PDF or scanned copy, and email it to `cla@wtrymiga.pl` before merge. Signing data is handled as described in the [CLA privacy notice](CLA_PRIVACY.md).

For the project's architecture and review rules, see `AGENTS.md`.
