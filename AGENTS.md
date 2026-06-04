# TiMini-Print Agent Guidelines

These notes are for contributors and coding agents working in this repository.
They describe public project architecture and PR expectations. Keep private
reverse-engineering notes, local source dumps, and machine-specific tooling out
of this repository.
Private local notes may live in ignored files such as `AGENTS.local.md`.

## PR Scope

- Keep one behavioral change per PR.
- Do not mix protocol fixes, transport fixes, documentation updates, and unrelated cleanup in one PR.
- Keep related cleanup in a separate commit; use a new PR only for independent follow-up work.
- Do not include opportunistic fixes found while working unless they are required for the main change; put them in a separate commit and call out why they are included.
- Do not add speculative support only because a model name looks similar.
- Do not add compatibility shims just to preserve old internal call paths.
- If a change is based on observed hardware behavior, add a regression test or a clear diagnostic path.

## Before Opening Or Merging A PR

- Check every code path touched by a new hook; do not test only the common path.
- If a hook is added after sending, test payload jobs and step-based jobs when both exist.
- Run the smallest relevant tests locally and state exactly what was run.
- Distinguish code/test verification from hardware verification.
- If a change has not been tested on hardware, say so explicitly.

## Hardware Reports

- If hardware behavior is verified, state the printer model, detected Bluetooth name, transport, firmware/app version if known, and exact before/after behavior.
- Do not generalize one model's behavior to a whole family unless the implementation models that distinction explicitly.
- Include relevant runtime/profile details when they affect the result, such as profile key, protocol family, protocol variant, runtime defaults, image encoding, chunk size, or delay.
- Completion waits should be passive unless the protocol has a documented status query that does not move paper or change printer state.

## Communication

- Keep PR descriptions and issue replies concise and factual.
- Lead with the result: what changed, what was tested, and what remains uncertain.
- Use bullets for hardware and test facts instead of long narrative paragraphs.
- Do not overclaim. Separate confirmed behavior from assumptions and hypotheses.
- If logs are long, summarize the relevant lines and attach or paste the full log separately.

## Architecture Boundaries

- `timiniprint.protocol` builds payloads and protocol jobs. It must not know transport.
- `timiniprint.rendering` owns pages, transforms, rasterization, and converters.
- `timiniprint.raster` owns shared raster types.
- `timiniprint.devices` owns catalog data, detection rules, profiles, and `PrinterDevice`.
- `timiniprint.printing` owns file-to-job flow, print settings, diagnostics, and runtime controllers.
- `timiniprint.transport` owns connect/send/disconnect only.
- Do not move family-specific protocol/session logic into transport adapters.

## Runtime And Transport

- Runtime protocol synchronization belongs in runtime controllers, not transport adapters.
- Transport code must not know printer opcodes, firmware states, or family-specific ACK semantics.
- BLE notification waits should be explicit runtime operations and covered across all send paths.
- GATT write response is not a printer protocol ACK.
- Completion waits should run after every successful send path, including step-based jobs.

## Protocol And Profiles

- Keep protocol families real. Do not merge unrelated wire protocols into one flag-heavy family.
- Reuse shared internals at the right level: codec, packet wrapper, raster recipe, or command dialect.
- Prefer explicit model fields such as `protocol_variant`, `paper_mode`, or runtime defaults over hidden heuristics.
- Detection rules should not silently route unknown models to unrelated profiles.
- If two source apps or hardware reports use the same advertised model name for different profiles or protocols, model both variants explicitly. Do not overwrite one source-backed profile with another source's values; leave automatic detection conservative and let advanced/manual selection choose the variant when needed.
- Add new models only when the protocol recipe matches the implementation.
- `paper_mode` means medium/recipe selection, not heating, density, or width.

## Code Style

- Prefer object-oriented boundaries at public package edges.
- Minimize generic helpers. Add a helper only when it closes a real domain concept.
- Keep low-level builders internal. Public API should stay centered on `PrinterDevice`, `PrinterProtocol`, `ProtocolJob`, and connectors.
- Avoid weakening package encapsulation for convenience.
- Wire-level comments are fine for packet semantics, constants, and thresholds.
- Do not add comments about private app internals or reverse-engineering sources in production code.

## Tests

- Test public contracts before private state.
- Add regression tests for fixed bugs.
- For runtime hooks, test both payload jobs and `ProtocolJob.steps` when both paths can apply.
- For catalog changes, test detection outcome and relevant runtime/profile settings, not only the profile key.
- Run targeted tests for the touched layer; run full `pytest` for broad protocol/runtime refactors.

## Documentation

- User-facing documentation belongs in `README.md` and `docs/`.
- `docs/protocol.md` is the first-contact guide for integrators.
- `docs/architecture.md` explains package boundaries and design.
- README should point to integration docs instead of duplicating them heavily.
- Keep private research notes and source-dump paths out of public documentation.

## Project Layout

- `timiniprint/app`: CLI and GUI entry points
- `timiniprint/data`: printer profiles, runtime defaults, detection rules, and README model groups
- `timiniprint/devices`: catalog, `PrinterDevice`, profile/config models, and transport targets
- `timiniprint/printing`: print job builder, settings, diagnostics, and runtime controllers
- `timiniprint/protocol`: public protocol API and internal family implementations
- `timiniprint/rendering`: page loading, transforms, and rasterization
- `timiniprint/transport`: connector/connection API and transport implementations
- `tests`: contract, integration, and regression tests
- `tools`: public audits and utility scripts
