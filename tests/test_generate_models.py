from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "tools/generate_models.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_models", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load generate_models module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tiny_args(
    model_no: str,
    *,
    size: int = 8,
    paper_size: int = 1600,
    print_size: int = 1728,
    tail: list[object] | None = None,
) -> list[object]:
    return [
        model_no,
        0,
        size,
        paper_size,
        print_size,
        8,
        f"{model_no}-",
        True,
        200,
        40,
        30,
        512,
        True,
        3,
        4,
        5000,
        5000,
        5000,
        8000,
        True,
        True,
        False,
        False,
        "0",
        0,
        *(tail or []),
    ]


def _java_token(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value)
    return str(value)


def _tiny_source(args: list[object]) -> str:
    return "class X { void f() { new PrinterModel.DataBean(" + ", ".join(
        _java_token(arg) for arg in args
    ) + "); } }"


class GenerateModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_module()

    def test_tiny_size_eight_defaults_to_source_left_padding(self) -> None:
        preset = self.tool.paper_preset_from_args(_tiny_args("X6H"))

        self.assertEqual(preset["render_width_px"], 1600)
        self.assertEqual(preset["paper_width_px"], 1664)
        self.assertEqual(preset["left_padding_px"], 64)

    def test_tiny_explicit_add_more_pix_num_overrides_default_padding(self) -> None:
        preset = self.tool.paper_preset_from_args(
            _tiny_args("X9", tail=[True, 30, 7300, 0, True, 32, 1, "2.1.1"])
        )

        self.assertEqual(preset["render_width_px"], 1600)
        self.assertEqual(preset["paper_width_px"], 1632)
        self.assertEqual(preset["left_padding_px"], 32)

    def test_tiny_p4_tail_reads_add_more_pix_num(self) -> None:
        preset = self.tool.paper_preset_from_args(
            _tiny_args("P4", tail=[True, 11000, 0, True, 24, 1])
        )

        self.assertEqual(preset["render_width_px"], 1600)
        self.assertEqual(preset["paper_width_px"], 1624)
        self.assertEqual(preset["left_padding_px"], 24)

    def test_tiny_size_eight_can_disable_protocol_padding(self) -> None:
        preset = self.tool.paper_preset_from_args(
            _tiny_args("GT08", tail=[True, 11000, 0, False])
        )

        self.assertEqual(preset["render_width_px"], 1600)
        self.assertEqual(preset["paper_width_px"], 1728)
        self.assertNotIn("left_padding_px", preset)

    def test_tiny_a4xii_tail_disables_protocol_padding(self) -> None:
        preset = self.tool.paper_preset_from_args(
            _tiny_args(
                "A41III",
                paper_size=1648,
                print_size=1664,
                tail=[True, 0, False, True],
            )
        )

        self.assertEqual(preset["render_width_px"], 1648)
        self.assertEqual(preset["paper_width_px"], 1664)
        self.assertNotIn("left_padding_px", preset)

    def test_tiny_non_eight_size_uses_wider_source_width_without_padding(self) -> None:
        preset = self.tool.paper_preset_from_args(
            _tiny_args(
                "15P3",
                size=1,
                paper_size=90,
                print_size=96,
                tail=[1, 1, False, True, 0.5, True, 0],
            )
        )

        self.assertEqual(preset["render_width_px"], 90)
        self.assertEqual(preset["paper_width_px"], 96)
        self.assertNotIn("left_padding_px", preset)

    def test_tiny_size_four_uses_paper_width_even_when_print_size_is_smaller(self) -> None:
        preset = self.tool.paper_preset_from_args(
            _tiny_args("Shipping Printer", size=4, paper_size=864, print_size=800)
        )

        self.assertEqual(preset["render_width_px"], 864)
        self.assertEqual(preset["paper_width_px"], 864)
        self.assertNotIn("left_padding_px", preset)

    def test_tiny_zp802_keeps_wider_output_without_protocol_padding(self) -> None:
        preset = self.tool.paper_preset_from_args(
            _tiny_args(
                "ZP802",
                paper_size=2400,
                print_size=2496,
                tail=[True, 30, 7300, 0, False, 36, 1, "1.0.3"],
            )
        )

        self.assertEqual(preset["render_width_px"], 2400)
        self.assertEqual(preset["paper_width_px"], 2496)
        self.assertNotIn("left_padding_px", preset)

    def test_tiny_x8_width_fallback_matches_source_special_case(self) -> None:
        self.assertEqual(self.tool.source_left_padding_px("X8-L", -1), 40)
        self.assertEqual(self.tool.source_left_padding_px("X8-W", -1), 40)
        self.assertEqual(self.tool.source_left_padding_px("X6H", -1), 64)

    def test_main_requires_explicit_source_and_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "PrintModelUtils.java"
            models_out = tmp_path / "models.json"
            paper_presets_out = tmp_path / "paper_presets.json"
            source.write_text(_tiny_source(_tiny_args("X9")), encoding="utf-8")

            self.tool.main(
                [
                    str(source),
                    "--models-out",
                    str(models_out),
                    "--paper-presets-out",
                    str(paper_presets_out),
                ]
            )

            models = json.loads(models_out.read_text(encoding="utf-8"))
            paper_presets = json.loads(paper_presets_out.read_text(encoding="utf-8"))

        self.assertEqual(models[0]["model_no"], "X9")
        self.assertEqual(models[0]["paper_presets"], ["default_1600r_1664p_64pl"])
        self.assertEqual(
            paper_presets["default_1600r_1664p_64pl"]["paper_width_px"],
            1664,
        )


if __name__ == "__main__":
    unittest.main()
