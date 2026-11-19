#!/usr/bin/env python3
"""Generate the manuscript's review-effect figure from analysis_real_summary.json."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUMMARY = ROOT / "data" / "analysis_real_summary.json"
DEFAULT_OUTPUT = ROOT / "figures" / "review_effects.png"
WIDTH = 2400
ROW_HEIGHT = 145
LEFT = 650
RIGHT = 170
TOP = 230
BOTTOM = 270
X_MIN = -0.20
X_MAX = 0.45
LABELS = {
    "time_to_merge_h": "Time to merge",
    "review_latency_h": "First review latency",
    "review_rounds": "Review rounds",
    "commits": "Commits",
    "pr_size": "Changed lines",
    "files_changed": "Files changed",
    "comments": "Issue comments",
    "review_comments": "Review comments",
}


def _font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def _x(value):
    plot_width = WIDTH - LEFT - RIGHT
    return LEFT + (value - X_MIN) / (X_MAX - X_MIN) * plot_width


def make_figure(summary_path=DEFAULT_SUMMARY, output_path=DEFAULT_OUTPUT):
    summary = json.loads(Path(summary_path).read_text())
    metrics = summary.get("metric_results")
    if not metrics:
        raise ValueError("analysis summary has no metric_results; rerun code/analyze_gha.py")
    unknown = [row["metric"] for row in metrics if row["metric"] not in LABELS]
    if unknown:
        raise ValueError(f"unknown metrics: {unknown}")
    outside = [
        row["metric"] for row in metrics
        if row["ci_95"][0] < X_MIN or row["ci_95"][1] > X_MAX
    ]
    if outside:
        raise ValueError(f"confidence intervals outside plot range: {outside}")

    height = TOP + ROW_HEIGHT * len(metrics) + BOTTOM
    image = Image.new("RGB", (WIDTH, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(64, bold=True)
    label_font = _font(42)
    tick_font = _font(34)
    note_font = _font(34)

    draw.text(
        (LEFT, 55),
        "Secondary rank-based outcomes",
        fill="#111827",
        font=title_font,
    )
    plot_top = TOP - 35
    plot_bottom = TOP + ROW_HEIGHT * len(metrics) - 45

    negligible_left = _x(-0.147)
    negligible_right = _x(0.147)
    draw.rectangle(
        (negligible_left, plot_top, negligible_right, plot_bottom),
        fill="#F1F5F9",
    )

    for tick in (-0.20, -0.10, 0.0, 0.10, 0.20, 0.30, 0.40):
        x_pos = _x(tick)
        color = "#334155" if tick == 0 else "#CBD5E1"
        width = 5 if tick == 0 else 2
        draw.line((x_pos, plot_top, x_pos, plot_bottom), fill=color, width=width)
        label = "0" if tick == 0 else f"{tick:+.2f}"
        box = draw.textbbox((0, 0), label, font=tick_font)
        draw.text(
            (x_pos - (box[2] - box[0]) / 2, plot_bottom + 32),
            label,
            fill="#334155",
            font=tick_font,
        )

    for index, row in enumerate(metrics):
        y_pos = TOP + index * ROW_HEIGHT + ROW_HEIGHT // 2
        label = LABELS[row["metric"]]
        draw.text((50, y_pos - 28), label, fill="#111827", font=label_font)
        low, high = row["ci_95"]
        delta = row["cliffs_delta"]
        low_x, high_x, delta_x = _x(low), _x(high), _x(delta)
        draw.line((low_x, y_pos, high_x, y_pos), fill="#2563EB", width=8)
        draw.line((low_x, y_pos - 17, low_x, y_pos + 17), fill="#2563EB", width=5)
        draw.line((high_x, y_pos - 17, high_x, y_pos + 17), fill="#2563EB", width=5)
        radius = 15
        draw.ellipse(
            (delta_x - radius, y_pos - radius, delta_x + radius, y_pos + radius),
            fill="#1D4ED8",
            outline="white",
            width=3,
        )
        value = f"{delta:+.3f}"
        draw.text((high_x + 20, y_pos - 22), value, fill="#1E3A8A", font=tick_font)

    note_y = plot_bottom + 105
    draw.text(
        (LEFT, note_y),
        "Cliff's delta with 95% percentile-bootstrap interval",
        fill="#334155",
        font=note_font,
    )
    draw.text(
        (LEFT, note_y + 50),
        "Positive values mean slower, larger, or more activity for AI-tagged pull requests.",
        fill="#334155",
        font=note_font,
    )
    draw.text(
        (LEFT, note_y + 100),
        "Shaded band: |delta| < 0.147, classified as negligible by the stated convention.",
        fill="#475569",
        font=note_font,
    )
    draw.text(
        (LEFT, note_y + 150),
        "Formal-review incidence uses percentage-point differences and is reported separately.",
        fill="#475569",
        font=note_font,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, dpi=(300, 300), optimize=True)
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = make_figure(args.summary, args.out)
    print(output)


if __name__ == "__main__":
    main()
