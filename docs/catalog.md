# Catalog Data Model

This document explains the JSON catalog used to turn printer names and user choices into a `PrinterDevice`. For runtime flow and examples, read [protocol.md](protocol.md). For package boundaries, read [architecture.md](architecture.md).

## Files

Catalog data lives in `timiniprint/data`:

- `printer_models.json`: supported printer models and their detection rules
- `printer_profiles.json`: shared printable parameter recipes
- `printer_paper_presets.json`: reusable paper/render geometry presets
- `origins.json`: stable source ID to human-readable source name mapping

`PrinterCatalog.load()` loads all of these files together and validates cross-references.

## Supported Models

A supported model entry represents a source-backed printer model that TiMini can print with. It contains:

- `model_key`: stable public model key used by CLI/configs/manual selection
- optional `marketing_names`: product/store/manual aliases
- optional `identity_names`: exact printer-reported names and protocol aliases, not Bluetooth detection rules
- `detections`: Bluetooth matching rules and optional aliases associated with each rule
- `origin_ids`: required, non-empty list of source IDs
- `profile_key`: shared printable profile recipe
- optional `protocol_override`, `image_pipeline_override`, and runtime override fields

Several model entries may point to the same profile when they use the same protocol recipe. If two sources use the same advertised Bluetooth name for different protocols or values, keep both variants explicit and let automatic detection stay conservative.

Marketing names never trigger automatic Bluetooth detection. They are public catalog names, so they are shown in model inventories and can be used for explicit CLI/GUI selection.

`identity_names` is used only after a protocol identity query. The catalog's
`device_from_identity(identity, origin_id=...)` performs case-insensitive exact
matching within the specified source, without trimming or using scan-name
heuristics. Protocol decoding/normalization belongs to the family. An unknown
or ambiguous identity raises `ValueError`; it never selects the first row.
Identity aliases do not automatically appear in the public model list. Add
them to `marketing_names` too when they are useful public selection names.

Every catalog entry is a printable `PrinterModel` with a required `profile_key`.
`detect_model(...)` returns a tuple of `ModelMatch` objects containing the model,
resolved profile, matching detection rule and contextual `origin_ids`. An empty
tuple means no recipe matched; multiple matches require explicit selection.
There is no separate catalog or matching result for known unsupported names.
Unknown Bluetooth endpoints remain available for manual selection/reporting.
Removing unsupported metadata does not add a driver: existing supported prefixes
and substrings can now match names previously annotated by that metadata.

## Sources

Every catalog model must have a non-empty `origin_ids`
list. A source may be an application, an SDK, or manufacturer documentation;
it does not have to be an Android app. IDs are opaque, stable strings. Existing
Android package names remain valid IDs, without implying a package-name format
for other sources. Each built-in ID must have a display name in `origins.json`.

Sources describe a model variant; they are not extra detection rules. If a name
is ambiguous, show the candidates with their source labels and let the user
select a `model_key`, using the existing model-selection flow:

```python
for match in catalog.detect_model(bluetooth_name):
    model = match.model
    print(model.model_key, catalog.origin_names(model.origin_ids))
```

`PrinterDevice.origin_ids` retains the selected model's sources, including after
exporting and importing a model-based printer config. A raw profile-based device
has no model attribution and may have an empty tuple. Custom catalogs without a
display-name registry show their source IDs directly.

### API migration

Use `PrinterModel` instead of `SupportedPrinterModel`, and `ModelMatch` instead
of `SupportedModelMatch`. The unsupported model/match types, catalog accessors,
constructor argument and `load(unsupported_model_path=...)` are removed, without
compatibility aliases. Construct custom catalogs as
`PrinterCatalog(profiles, models, origin_names=...)`; optional paper/source paths
in `load()` are keyword-only. Existing printable printer configs need no changes.
Consumers that imported the removed types must be updated before using this core.

The obsolete `detection_ambiguity_group` field is also removed. It no longer
controlled runtime detection and no built-in same-source tie required it.

Integrators must rename `origin_app_packages` to `origin_ids` and
`PrinterCatalog.origin_app_names(...)` to `PrinterCatalog.origin_names(...)`.
The constructor's mapping argument is also `origin_names`; `load()` accepts
`origin_path` instead of `origin_app_path`. The registry is now `origins.json`.
No aliases for the old names are provided. Update consumers before advancing
their backend revision. Existing model-based printer config files need no
migration: they store `model_key`, not source metadata.

## Detection Rules

Automatic detection uses flat detection objects. A manual-only model may use
`"detections": []` with non-empty model-level `marketing_names`: it appears in
the catalogue and can be selected by name or model key, but never matches a
Bluetooth advertisement.

A detection may also carry `marketing_names` when an alias belongs specifically
to that rule:

```json
{
  "marketing_names": ["Product family"],
  "detections": [
    {
      "exact_names": ["BT-01"],
      "prefixes": ["BT01-"],
      "substrings": ["Core"],
      "excluded_prefixes": ["BT-01 Pro"],
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
- `suffixes`: normalized advertised name must end with the value; a literal
  space requires model-level `whitespace_mode: "preserve"`
- `mac_prefixes`: optional required address-prefix alternatives
- `mac_suffixes`: optional address suffix filter
- `excluded_mac_suffixes`: optional address suffixes which reject the rule
- `excluded_prefixes`: optional advertised-name prefixes which reject the rule;
  they use the model's whitespace and case policy and are not public names
- `manufacturer_data_suffixes`: required alternative hexadecimal suffixes of
  BLE manufacturer AD data, including the two-byte company identifier
- `manufacturer_data_lengths`: optional allowed lengths of that AD data in
  bytes; requires `manufacturer_data_suffixes`
- `name_pattern`: optional full-name regular expression, applied after the
  model's whitespace normalization and using its case policy
- `all_of`: require every populated name-trigger group in this detection

By default, `exact_names`, `prefixes`, `substrings`, and `suffixes` are alternative trigger
groups. With `all_of: true`, every populated group must match, while values
inside each array remain alternatives. MAC constraints are additional in both
modes: a configured prefix and suffix must both match, while any excluded
suffix rejects the rule. Rules with MAC constraints require a real Bluetooth
MAC address and do not match UUID-style platform identifiers.

Manufacturer constraints require actual advertisement data; a MAC suffix is
not a substitute. Supply it as `BluetoothEndpoint.manufacturer_data` (a tuple
of byte strings), or pass `manufacturer_data=...` to catalog detection.
Bleak discovery preserves it automatically. Mobile adapters must forward the
same raw data; if absent, manufacturer-constrained rules do not match.

When a `BluetoothTarget` is supplied, `detect_device()` and `detection_devices()`
use its stored advertisement evidence: BLE first, then evidence retained on a
Classic peer. A nonempty `manufacturer_data` argument takes precedence. The
target does not infer or manufacture advertisement bytes.

`name_pattern` is an additional condition when literal triggers are present;
by itself it is a name trigger and needs an explicit `marketing_names` alias.
Patterns and manufacturer bytes are not public model names.

Each model may set `whitespace_mode` to control how its detections compare the
advertised name. `remove` is the default and removes all whitespace, `trim`
removes only leading and trailing whitespace, and `preserve` compares the raw
name. Discovery keeps the raw name for matching; presentation may trim its own
copy after a model has been selected.

Matching is resolved independently for each source (`origin_id`: an app, SDK,
or documentation source). Within that source, longer and more constrained
rules win over broader rules. Case-sensitive matching is preferred, with
case-folded fallback only when that source has no case-sensitive match.

The catalog combines source-local results and deduplicates model keys. A
longer prefix or exact spelling from one source cannot
remove another source's matching recipe. `detect_device(...)` returns a device
only when the combined result is exactly one model; otherwise the
caller asks the user to choose the source/model explicitly.

`match.model.origin_ids` describes the catalog model's source associations;
`match.origin_ids` contains only the sources for which this endpoint matched.
Devices made from matches retain this narrower source context. Exported
printer configs preserve it in `device.origin_ids`; omitting that optional
field uses the catalog model's source metadata.

Names that an app rejects or redirects elsewhere are not alternative print
recipes. Keep unresolved assignments in documentation, not as non-printable
catalog entries. Same-source ties are reported by the audit; matching has no
per-model ambiguity exemptions.

Raw trigger spelling is preserved for the public catalog. Private matching copies apply the model's `whitespace_mode`. Case-sensitive matching is preferred; fallback case-folded matching exists for platform scan quirks and should not be used as an excuse for sloppy data.

Public model names are the ordered union of model-level `marketing_names` and, for each detection in order, its `marketing_names`, `exact_names`, `prefixes`, and standalone `substrings`/`suffixes`. Substrings and suffixes used as `all_of` constraints are not published as model names. One trailing `-` or `_` is removed from pattern display names. Values are deduplicated case-insensitively while preserving the first spelling and order; whitespace remains significant for display, so aliases such as `PM241` and `PM 241` may both remain searchable. MAC constraints are not public model names.

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

A profile alone does not include Bluetooth detection metadata, source metadata, model-specific overrides, or transport target. Prefer model-based configs for normal use.

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
- optional `raster_width_px`: exact final raster width, independent of the protocol paper width; content is centered and oversized raw rasters are rejected, not cropped
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

If `model_key` is present, deleting an override falls back to the current catalog model. Raw profile-based configs are possible for low-level diagnostics but do not carry model detection or source metadata.

## README Rendering

README model lists are generated from the same public-name union used by manual
catalog lookup. Grouping comes from printable model entries, without a second
presentation-only inventory or a list of unimplemented names.

## Audit Rules

Identical model/profile bodies are errors. Distinct models may intentionally
share a recipe, so `mergeable_model_body` is only an advisory warning. It must
not force removal of public model keys or invent distinctions in model data.

Catalog changes should be checked by tests or `tools/catalog_audit.py` when they affect:

- duplicate model keys
- detection conflicts
- missing source IDs or names
- profile references
- paper preset references
- README model inventory output
