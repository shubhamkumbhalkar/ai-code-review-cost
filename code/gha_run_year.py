#!/usr/bin/env python3
"""Drive a full-year real sample from GH Archive: one contiguous 12-hour block per month across the
12 most recent REAL months (2024-10 .. 2025-09; GH Archive has no data after ~2025-09). Within each
month's block the PR<->push<->review join is computed (join must be within the block), then rows are
appended to one combined dataset with a 'month' column. Checkpointed per month (resumable).

Process-and-discard is inherited from gharchive_collect, so disk stays small.
Output: data/gha_prs.csv  (resume marker: data/gha_months_done.json)
"""
import csv
import datetime as dt
import json
from pathlib import Path

import gharchive_collect as g

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "gha_prs.csv"
DONE = ROOT / "data" / "gha_months_done.json"
COLS = ["month"] + g.COLS

# 12 real months; block = day 15, hours 06..17 (12 contiguous hours)
MONTHS = [(2024, m) for m in (10, 11, 12)] + [(2025, m) for m in range(1, 10)]
BLOCK_START_HOUR = 6
BLOCK_HOURS = 12


def main():
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    new = not OUT.exists()
    f = OUT.open("a", newline="")
    w = csv.DictWriter(f, fieldnames=COLS)
    if new:
        w.writeheader()
    for (y, m) in MONTHS:
        tag = f"{y}-{m:02d}"
        if tag in done:
            print(f"skip done: {tag}"); continue
        start = f"{y:04d}-{m:02d}-15-{BLOCK_START_HOUR}"
        keys = [g._fmt(dt.datetime.strptime(start, "%Y-%m-%d-%H") + dt.timedelta(hours=i))
                for i in range(BLOCK_HOURS)]
        print(f"\n=== month {tag}: {keys[0]} .. {keys[-1]} ===")
        prs, reviews, ai_shas, all_push = g.collect(keys)
        rows = g.build_rows(prs, reviews, ai_shas, all_push)
        link = sum(r["linkable"] for r in rows); ai = sum(r["is_ai"] for r in rows)
        for r in rows:
            r = {"month": tag, **r}
            w.writerow(r)
        f.flush()
        print(f"  {tag}: {len(rows)} PRs | linkable {link} ({100*link/max(len(rows),1):.0f}%) | AI {ai}")
        done.add(tag); DONE.write_text(json.dumps(sorted(done)))
    f.close()
    print("\nyear collection done.")


if __name__ == "__main__":
    main()
