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
- optional `marketing_names`: product/store/manual aliases
- `detections`: Bluetooth matching rules and optional aliases associated with each rule
- `origin_app_packages`: source app package names
- `profile_key`: shared printable profile recipe
- optional `protocol_override`, `image_pipeline_override`, and runtime override fields

Several model entries may point to the same profile when they use the same protocol recipe. If two source apps use the same advertised Bluetooth name for different protocols or values, keep both variants explicit and let automatic detection stay conservative.

Marketing names never trigger automatic Bluetooth detection. They are public catalog names, so they are shown in model inventories and can be used for explicit CLI/GUI selection.

## Unsupported Models

Unsupported model entries use the same base identity/detection shape as supported models, but they do not reference an implemented `PrinterProfile`.

Use unsupported entries to:

- recognize future support candidates in scans and reports
- prevent a broad supported prefix from stealing an unrelated known model
- group README future-support names

`profile_key_prediction`, when present, is a future extraction/grouping hint. It is not an implemented profile key and must not route hardware to a protocol.

## Detection Rules

Each model has one or more flat detection objects. A detection may also carry `marketing_names` when an alias belongs specifically to that rule:

```json
{
  "marketing_names": ["Product family"],
  "detections": [
    {
      "exact_names": ["BT-01"],
      "prefixes": ["BT01-"],
      "substrings": ["Core"],
      "mac_prefixes": ["13:03"],
      "mac_suffixes": ["59"],
      "excluded_mac_suffixes": ["00"],
      "marketing_names": ["Retail model"]
    }
  ]
}
```

Put alternative triggers with the same MAC constraint and the same rule-specific marketing names in the arrays of one detection object. Add another detection object only when those constraints or associations differ.

Detection supports:

- `exact_names`: normalized advertised name must match exactly
- `prefixes`: normalized advertised name must start with the prefix
- `substrings`: normalized advertised name must contain the value
- `mac_prefixes`: optional required address-prefix alternatives
- `mac_suffixes`: optional address suffix filter
- `excluded_mac_suffixes`: optional address suffixes which reject the rule
- `all_of`: require every populated name-trigger group in this detection

By default, `exact_names`, `prefixes`, and `substrings` are alternative trigger
groups. With `all_of: true`, every populated group must match, while values
inside each array remain alternatives. MAC constraints are additional in both
modes: a configured prefix and suffix must both match, while any excluded
suffix rejects the rule. Rules with MAC constraints require a real Bluetooth
MAC address and do not match UUID-style platform identifiers.

Each model may set `whitespace_mode` to control how its detections compare the
advertised name. `remove` is the default and removes all whitespace, `trim`
removes only leading and trailing whitespace, and `preserve` compares the raw
name. Discovery keeps the raw name for matching; presentation may trim its own
copy after a model has been selected.

Matching is sorted by specificity. Longer and more constrained rules win over broader rules. Supported matches win over unsupported matches at equal specificity. If multiple supported models tie, automatic `detect_device(...)` returns `None` so the caller can ask the user to choose a model/source explicitly.

Raw trigger spelling is preserved for the public catalog. Private matching copies apply the model's `whitespace_mode`. Case-sensitive matching is preferred; fallback case-folded matching exists for platform scan quirks and should not be used as an excuse for sloppy data.

Public model names are the ordered union of model-level `marketing_names` and, for each detection in order, its `marketing_names`, `exact_names`, `prefixes`, and standalone `substrings`. Substrings used as `all_of` constraints are not published as model names. One trailing `-` or `_` is removed from prefix/substring display names. Values are deduplicated case-insensitively while preserving the first spelling and order; whitespace remains significant for display, so aliases such as `PM241` and `PM 241` may both remain searchable. MAC constraints are not public model names.

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
- optional `top_padding_px`: leading blank raster rows reserved before rendered content
- optional `mirror_horizontal`: mirror the complete paper raster before protocol encoding
- optional `rotation_degrees`: clockwise source rotation; one of `0`, `90`, `180`, or `270`
- optional `dither_mode`: paper-specific raster dithering recipe
- optional `render_height_scale`: vertical content scale applied before fixed-height fitting
- optional `paper_mode`: low-level protocol recipe selector
- optional `max_height_px`: sheet/page height cap when the protocol needs one

High-level callers select paper through `PrintSettings(paper_preset_key=...)`. They should not select low-level `paper_mode` directly. `paper_mode` exists because some wire protocols change feed/end-page behavior depending on medium type.

If `paper_width_px` is wider than `render_width_px` and `left_padding_px` is zero, the printing layer centers the rendered page on a white canvas. If `left_padding_px` is set, the protocol builder applies that padding and the raster remains at `render_width_px`.

`render_height_px` and `raster_height_px` model different stages. The first constrains file/text rendering. The second describes the final raster sent to the protocol builder. When both are present, `render_height_px + top_padding_px` must not exceed `raster_height_px`. Remaining rows are padded at the trailing edge. A raw raster that does not fit together with its leading padding is rejected instead of being cropped.

Paper mirroring and rotation are rendering/layout operations, not image-codec flags. The paper rotation is combined with the user's optional 90-degree rotation before rasterization. File previews and file printing receive the same transform. Callers that build jobs from an already prepared `RasterSet` receive paper mirroring from the printing layer, but the raster is otherwise assumed to have its intended orientation already.

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

README model lists are generated from the same public-name union used by manual catalog lookup. Supported and unsupported lists should not duplicate separate presentation-only source files. Grouping should be derived from model entries, `profile_key`, or `profile_key_prediction` depending on whether the model is implemented.

## Audit Rules

Catalog changes should be checked by tests or `tools/catalog_audit.py` when they affect:

- duplicate model keys
- detection conflicts
- missing origin app names
- unsupported/support overlap
- profile references
- paper preset references
- README model inventory output
