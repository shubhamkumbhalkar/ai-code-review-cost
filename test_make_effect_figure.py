import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))
import make_effect_figure


class EffectFigureTests(unittest.TestCase):
    def test_generates_png_from_metric_results(self):
        summary = {
            "metric_results": [
                {
                    "metric": "time_to_merge_h",
                    "cliffs_delta": 0.13,
                    "ci_95": [0.11, 0.15],
                },
                {
                    "metric": "review_latency_h",
                    "cliffs_delta": 0.04,
                    "ci_95": [0.00, 0.08],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "summary.json"
            output = Path(directory) / "figure.png"
            source.write_text(json.dumps(summary))
            result = make_effect_figure.make_figure(source, output)
            self.assertEqual(output, result)
            self.assertTrue(output.exists())
            with Image.open(output) as image:
                self.assertEqual("PNG", image.format)
                self.assertEqual(make_effect_figure.WIDTH, image.width)
                self.assertEqual(
                    make_effect_figure.TOP
                    + 2 * make_effect_figure.ROW_HEIGHT
                    + make_effect_figure.BOTTOM,
                    image.height,
                )
                self.assertGreaterEqual(image.info["dpi"][0], 299)

    def test_accepts_final_positive_interval_range(self):
        summary = {
            "metric_results": [
                {
                    "metric": "pr_size",
                    "cliffs_delta": 0.359705958,
                    "ci_95": [0.331, 0.387],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "summary.json"
            output = Path(directory) / "figure.png"
            source.write_text(json.dumps(summary))
            make_effect_figure.make_figure(source, output)
            self.assertTrue(output.exists())

    def test_rejects_interval_outside_configured_range(self):
        summary = {
            "metric_results": [
                {
                    "metric": "pr_size",
                    "cliffs_delta": 0.46,
                    "ci_95": [0.44, 0.46],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "summary.json"
            source.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "outside plot range"):
                make_effect_figure.make_figure(source, Path(directory) / "figure.png")

    def test_rejects_summary_without_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "summary.json"
            source.write_text("{}")
            with self.assertRaisesRegex(ValueError, "no metric_results"):
                make_effect_figure.make_figure(source, Path(directory) / "figure.png")


if __name__ == "__main__":
    unittest.main()
