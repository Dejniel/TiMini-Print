# Catalog Data Model

This document explains the JSON catalog used to turn printer names and user choices into a `PrinterDevice`. For runtime flow and examples, read [protocol.md](protocol.md). For package boundaries, read [architecture.md](architecture.md).

## Files

Catalog data lives in `timiniprint/data`:

- `printer_models.json`: supported printer models and their detection rules
- `printer_models_unsupported.json`: known-but-not-implemented models
- `printer_profiles.json`: shared printable parameter recipes
- `printer_paper_presets.json`: reusable paper/render geometry presets
- `origin_apps.json`: Android package to human app name mapping

`PrinterCatalog.load()` loads all of these files together and validates cross-references.

## Supported Models

A supported model entry represents a source-backed printer model that TiMini can print with. It contains:

- `model_key`: stable public model key used by CLI/configs/manual selection
- optional `marketing_name`: README-only product/store/manual name
- `detections`: Bluetooth names, prefixes, and optional MAC suffix filters
- `origin_app_packages`: source app package names
- `profile_key`: shared printable profile recipe
- optional `protocol_override`, `image_pipeline_override`, and runtime override fields

Several model entries may point to the same profile when they use the same protocol recipe. If two source apps use the same advertised Bluetooth name for different protocols or values, keep both variants explicit and let automatic detection stay conservative.

`marketing_name` is presentation metadata only. It must not be used as a Bluetooth detection trigger, CLI alias, GUI selector, or routing hint.

## Unsupported Models

Unsupported model entries use the same base identity/detection shape as supported models, but they do not reference an implemented `PrinterProfile`.

Use unsupported entries to:

- recognize future support candidates in scans and reports
- prevent a broad supported prefix from stealing an unrelated known model
- group README future-support names

`profile_key_prediction`, when present, is a future extraction/grouping hint. It is not an implemented profile key and must not route hardware to a protocol.

## Detection Rules

Each model has named detections. The displayed/public model name is attached to its own detection rule instead of being global metadata.

Detection supports:

- `exact_names`: normalized advertised name must match exactly
- `prefixes`: normalized advertised name must start with the prefix
- `mac_suffixes`: optional address suffix filter

Matching is sorted by specificity. Longer and more constrained rules win over broader rules. Supported matches win over unsupported matches at equal specificity. If multiple supported models tie, automatic `detect_device(...)` returns `None` so the caller can ask the user to choose a model/source explicitly.

Name normalization removes whitespace. Case-sensitive matching is preferred; fallback case-folded matching exists for platform scan quirks and should not be used as an excuse for sloppy data.

## Profiles

`printer_profiles.json` contains shared print recipes. A `PrinterProfile` is not a model and not a connected printer. It describes default values that can be reused by multiple supported models:

- protocol default family and packets type
- image pipeline default
- stream chunk size and delay
- print defaults such as energy, speed, and density
- runtime presets and capabilities
- supported paper presets
- BLE MTU request
- legacy protocol flags that are still profile-level behavior

A profile alone does not include Bluetooth detection metadata, source app metadata, model-specific overrides, or transport target. Prefer model-based configs for normal use.

## Runtime Settings

Runtime settings describe stateful session behavior that cannot live in static profile values alone:

- `control_algorithm`: which runtime controller algorithm to use
- `preset`: density/capability defaults used by that algorithm
- `capabilities`: notification/status features known from catalog data

`prepare_connection_runtime(...)` selects a runtime controller from the resolved `PrinterDevice`. For families that do not need one, it returns an empty context. For runtime-sensitive families, it may attach a controller, query capabilities, run a handshake, or subscribe to notifications depending on transport support.

## Paper Presets

`printer_paper_presets.json` stores reusable paper choices. Profiles reference presets by exact key, so repeated geometry is not copied across every profile.

A `PaperPreset` contains:

- `key`: stable preset key used in configs and CLI/GUI selection
- `label`: user-facing label
- `paper_width_px`: full protocol paper/canvas width, including margins
- `render_width_px`: width rendered by file/raster pipeline before protocol padding/centering
- optional `render_height_px`: fixed content area height; taller pages are scaled to fit and text pagination uses this height
- optional `raster_height_px`: exact final raster height; shorter output is padded with white rows at the trailing edge
- optional `left_padding_px`: protocol-side left padding
- optional `paper_mode`: low-level protocol recipe selector
- optional `max_height_px`: sheet/page height cap when the protocol needs one

High-level callers select paper through `PrintSettings(paper_preset_key=...)`. They should not select low-level `paper_mode` directly. `paper_mode` exists because some wire protocols change feed/end-page behavior depending on medium type.

If `paper_width_px` is wider than `render_width_px` and `left_padding_px` is zero, the printing layer centers the rendered page on a white canvas. If `left_padding_px` is set, the protocol builder applies that padding and the raster remains at `render_width_px`.

`render_height_px` and `raster_height_px` model different stages. The first constrains file/text rendering. The second describes the final raster sent to the protocol builder. When both are present, `render_height_px` must not exceed `raster_height_px`. A raw raster taller than `raster_height_px` is rejected instead of being cropped.

## Printer Configs

Editable printer configs are serialized `PrinterDevice` descriptions. They can store:

- `model_key` fallback when the device came from a supported model
- `profile_key` for the shared recipe underneath
- full editable `profile_overrides`
- protocol family/type and packets type
- image pipeline override
- runtime overrides
- optional transport target

If `model_key` is present, deleting an override falls back to the current catalog model. Raw profile-based configs are possible for low-level diagnostics but do not carry model detection or source-app metadata.

## README Rendering

README model lists are generated from catalog data. Supported and unsupported lists should not duplicate separate presentation-only source files. README-only grouping should be derived from model entries, `marketing_name`, `profile_key`, or `profile_key_prediction` depending on whether the model is implemented.

## Audit Rules

Catalog changes should be checked by tests or `tools/catalog_audit.py` when they affect:

- duplicate model keys
- detection conflicts
- missing origin app names
- unsupported/support overlap
- profile references
- paper preset references
- README model inventory output
