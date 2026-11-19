#!/usr/bin/env python3
"""Build the fixed Oct. 2024 through Sep. 2025 GH Archive review sample atomically.

Each month uses the 15th from 06:00 through 17:59 UTC. Every requested hour must download and parse.
The existing output remains unchanged until all twelve monthly blocks are complete.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path

import gharchive_collect as collector

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "gha_prs.csv"
COLS = ["month"] + collector.COLS
MONTHS = [(2024, month) for month in (10, 11, 12)] + [
    (2025, month) for month in range(1, 10)
]
BLOCK_START_HOUR = 6
BLOCK_HOURS = 12
CONTEXT_HOURS = 1


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(output):
    output = Path(output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    month_records = []
    totals = {"rows": 0, "linkable": 0, "ai": 0}

    with tempfile.NamedTemporaryFile(
        mode="w", newline="", suffix=".csv", prefix="gha_prs_", dir=output.parent, delete=False
    ) as handle:
        temp_output = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=COLS)
        writer.writeheader()
        try:
            for year, month in MONTHS:
                tag = f"{year}-{month:02d}"
                start = dt.datetime(year, month, 15, BLOCK_START_HOUR)
                hours = [
                    collector._fmt(start + dt.timedelta(hours=index))
                    for index in range(BLOCK_HOURS + CONTEXT_HOURS)
                ]
                analysis_end = start + dt.timedelta(hours=BLOCK_HOURS)
                print(
                    f"\n=== {tag}: analysis {hours[0]} through "
                    f"{collector._fmt(analysis_end - dt.timedelta(hours=1))}; "
                    f"context through {hours[-1]} ===",
                    flush=True,
                )
                analysis_hours = hours[:BLOCK_HOURS]
                prs, reviews, ai_shas, all_push, hour_records = collector.collect(
                    hours, analysis_hour_keys=analysis_hours
                )
                rows = collector.build_rows(prs, reviews, ai_shas, all_push)
                linkable = sum(row["linkable"] for row in rows)
                ai = sum(row["is_ai"] for row in rows if row["linkable"])
                for row in rows:
                    writer.writerow({"month": tag, **row})
                handle.flush()
                month_records.append(
                    {
                        "month": tag,
                        "block_start_utc": start.replace(tzinfo=dt.timezone.utc).isoformat(),
                        "block_end_exclusive_utc": (
                            start + dt.timedelta(hours=BLOCK_HOURS)
                        ).replace(tzinfo=dt.timezone.utc).isoformat(),
                        "hours": hour_records,
                        "rows": len(rows),
                        "linkable": linkable,
                        "ai": ai,
                    }
                )
                totals["rows"] += len(rows)
                totals["linkable"] += linkable
                totals["ai"] += ai
                print(
                    f"  month complete: {len(rows):,} PRs | linkable {linkable:,} | AI {ai:,}",
                    flush=True,
                )
        except Exception:
            temp_output.unlink(missing_ok=True)
            raise

    csv_bytes = temp_output.stat().st_size
    csv_sha256 = file_sha256(temp_output)
    manifest = {
        "status": "complete",
        "design": "fixed monthly blocks",
        "block_start_hour_utc": BLOCK_START_HOUR,
        "block_hours": BLOCK_HOURS,
        "context_hours_per_block": CONTEXT_HOURS,
        "analysis_hours": len(MONTHS) * BLOCK_HOURS,
        "context_hours": len(MONTHS) * CONTEXT_HOURS,
        "requested_hours": len(MONTHS) * (BLOCK_HOURS + CONTEXT_HOURS),
        "completed_hours": sum(len(record["hours"]) for record in month_records),
        "csv_bytes": csv_bytes,
        "csv_sha256": csv_sha256,
        "months": month_records,
        "totals": totals,
    }
    if manifest["completed_hours"] != manifest["requested_hours"]:
        temp_output.unlink(missing_ok=True)
        raise RuntimeError(
            f"incomplete collection: {manifest['completed_hours']} of "
            f"{manifest['requested_hours']} hours"
        )

    os.replace(temp_output, output)
    temp_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temp_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    os.replace(temp_manifest, manifest_path)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    manifest = build(args.out)
    totals = manifest["totals"]
    print(
        f"\ncomplete: {totals['rows']:,} PRs | linkable {totals['linkable']:,} | "
        f"AI {totals['ai']:,}",
        flush=True,
    )


if __name__ == "__main__":
    main()
