import pytest

from timiniprint.devices.profiles import ModelDetection, PrinterModel, WhitespaceMode
from timiniprint.devices.model_codec import model_from_json, model_to_json
from tools.catalog_audit import _sample_names


def test_suffix_constrains_the_whole_name_and_beats_a_bare_prefix():
    rule = ModelDetection(prefixes=("Unit",), suffixes=(" ",), all_of=True)
    plain = ModelDetection(prefixes=("Unit",))
    kwargs = dict(whitespace_mode=WhitespaceMode.PRESERVE)
    for name in ("Unit ", "Unit-123 "):
        assert rule.matches(name, None, **kwargs)
        assert rule.matched_specificity(name, None, **kwargs) > plain.matched_specificity(name, None)
    for name in ("Unit", "Unit -123", "Other "):
        assert not rule.matches(name, None, **kwargs)
    assert not rule.matches("Unit ", None)  # Never match an empty normalized suffix.
    assert rule.names == ("Unit",)


def test_suffix_or_and_case_folding():
    rule = ModelDetection(prefixes=("Unit",), suffixes=("-Pro",))
    assert rule.matches("Unit", None)
    assert rule.matches("Other-Pro", None)
    assert not rule.matches("Other-pro", None)
    assert rule.matches("Other-pro", None, case_sensitive=False)
    assert not rule.matches("Other-Professional", None)


def test_suffix_model_json_roundtrip_and_whitespace_validation():
    data = dict(model_key="unit", origin_ids=["test"], whitespace_mode="preserve",
                detections=[dict(prefixes=["Unit"], suffixes=[" "], all_of=True)])
    model = model_from_json(PrinterModel, data)
    assert model_from_json(PrinterModel, model_to_json(model)) == model
    for mode in ("trim", "remove"):
        with pytest.raises(ValueError, match="Whitespace-only"):
            model_from_json(PrinterModel, dict(data, whitespace_mode=mode))
    with pytest.raises(ValueError, match="empty"):
        ModelDetection(prefixes=("Unit",), suffixes=("",))
    samples = _sample_names(data)
    assert samples == ["Unit "]
    assert all(model.detections[0].matches(name, None, whitespace_mode=model.whitespace_mode)
               for name in samples)
