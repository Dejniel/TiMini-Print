# Contributing

Thank you for helping improve TiMini-Print. Keep each pull request focused on
one behavioral change and include the smallest relevant tests.

## Contribution terms

By submitting a contribution, you represent that you have the right to submit
it and agree that it is licensed under the Apache License 2.0.

Do not submit source code, assets, data, credentials, personal data, unique
device identifiers, or other material copied from manufacturer applications,
firmware, SDKs, or other proprietary sources. Do not attach proprietary
binaries, decompiled sources, private source dumps, or packet captures that
contain identifiers.

Protocol contributions must be independently implemented from lawful
observations, public documentation, or contributor-owned test data. Describe
the provenance, affected printer model, transport, and test method in the pull
request. Sanitize Bluetooth addresses, serial numbers, credentials, and other
device-specific identifiers from logs and fixtures.

If the contribution was created as part of your employment or for another
organization, make sure you have any permission required to submit it.

## Verification

- Run the smallest relevant test set and state the exact command in the pull
  request.
- Add a regression test for a fixed bug.
- Distinguish code/test verification from physical printer verification.
- For hardware verification, include the model, detected Bluetooth name,
  transport, relevant profile/runtime settings, and exact before/after result.
- Do not generalize behavior from one printer to a whole family without
  evidence that the family shares it.

For more project-specific architecture and review rules, see `AGENTS.md`.
