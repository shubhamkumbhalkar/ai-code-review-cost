#!/usr/bin/env python3
"""Downstream-outcome collector: over a CONTIGUOUS date range of GH Archive hours, extract merged
PRs plus revert signals, so we can measure how often AI-assisted vs non-AI PRs get reverted.

Memory-safe (no giant all-push-SHA set): we keep only
  - ai_shas       : commit SHAs carrying a strict AI trailer (small; ~0.5% of commits)
  - reverted_shas : SHAs named in a "This reverts commit <sha>" message
  - revert_prnums : (repo, number) from "revert #N" messages / revert-PR titles
  - prs           : one record per merged PR (repo, num, merge_sha, head_sha, metrics, merged_at)

AI flag per PR  : merge_sha or head_sha in ai_shas (conservative tip-SHA match, same as main study).
Revert flag     : merge_sha in reverted_shas (exact or short-prefix) OR (repo,num) in revert_prnums.

Reverts happen days after a merge, so a contiguous window is required; PRs merged late in the window
have less forward observation (right-censoring) and are handled in analysis, not here.

Usage: python3 gha_reverts.py --start 2025-08-01 --days 30 --out data/gha_reverts.csv
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import detect_ai_commits as det

ROOT = Path(__file__).resolve().parent.parent
REV_SHA = re.compile(r"this reverts commit ([0-9a-f]{7,40})", re.I)
REV_NUM = re.compile(r"\brevert(?:s|ed)?\b[^\n]*?#(\d{1,7})", re.I)
COLS = ["repo", "pr", "is_ai", "reverted", "time_to_merge_h", "review_rounds", "commits",
        "pr_size", "files_changed", "comments", "author_assoc", "merged_at"]


def _fmt(t):
    return f"{t.year:04d}-{t.month:02d}-{t.day:02d}-{t.hour}"


def _hours(a, b):
    try:
        da = dt.datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = dt.datetime.fromisoformat(b.replace("Z", "+00:00"))
        return round((db - da).total_seconds() / 3600, 3)
    except Exception:
        return None


def process_hour(hk, pr_fh, ai_shas, reverted_full, reverted_short, revert_prnums):
    url = f"https://data.gharchive.org/{hk}.json.gz"
    with tempfile.NamedTemporaryFile(suffix=".gz", delete=False) as tf:
        path = tf.name
    try:
        r = subprocess.run(["curl", "-s", "-o", path, "--max-time", "180", url], capture_output=True)
        if r.returncode != 0 or os.path.getsize(path) < 1_000_000:
            return 0
        n = 0
        with gzip.open(path) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                t = e.get("type"); repo = e.get("repo", {}).get("name")
                if t == "PullRequestEvent":
                    pl = e["payload"]
                    if pl.get("action") == "closed" and pl["pull_request"].get("merged"):
                        p = pl["pull_request"]
                        pr_fh.write(json.dumps({
                            "repo": repo, "pr": p["number"],
                            "merge_sha": (p.get("merge_commit_sha") or "").lower(),
                            "head_sha": ((p.get("head") or {}).get("sha") or "").lower(),
                            "time_to_merge_h": _hours(p.get("created_at"), p.get("merged_at")),
                            "commits": p.get("commits"), "pr_size": (p.get("additions", 0) + p.get("deletions", 0)),
                            "files_changed": p.get("changed_files"), "comments": p.get("comments"),
                            "author_assoc": p.get("author_association"), "merged_at": p.get("merged_at"),
                        }) + "\n")
                    elif pl.get("action") == "opened":
                        p = pl["pull_request"]; title = (p.get("title") or "")
                        if title.lower().startswith("revert"):
                            for mnum in REV_NUM.findall(title + " " + (p.get("body") or "")[:200]):
                                revert_prnums.add((repo, int(mnum)))
                elif t == "PullRequestReviewEvent":
                    pass  # review rounds handled lightly below via 'comments'; keep memory low
                elif t == "PushEvent":
                    for c in e["payload"].get("commits", []):
                        msg = c.get("message") or ""
                        sha = c.get("sha")
                        if sha and det.classify(msg):
                            ai_shas.add(sha)
                        m = REV_SHA.search(msg)
                        if m:
                            s = m.group(1).lower()
                            (reverted_full if len(s) >= 40 else reverted_short).add(s)
                        for mnum in REV_NUM.findall(msg[:300]):
                            revert_prnums.add((repo, int(mnum)))
                n += 1
        return n
    finally:
        try: os.remove(path)
        except OSError: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="data/gha_reverts.csv")
    args = ap.parse_args()
    start = dt.datetime.strptime(args.start, "%Y-%m-%d")
    hours = [_fmt(start + dt.timedelta(hours=i)) for i in range(args.days * 24)]
    print(f"contiguous window: {hours[0]} .. {hours[-1]} ({len(hours)} hours)", flush=True)

    ai_shas = set(); reverted_full = set(); reverted_short = set(); revert_prnums = set()
    pr_jsonl = ROOT / "data" / "_reverts_prs.jsonl"
    pr_fh = pr_jsonl.open("w")
    npr = 0
    for i, hk in enumerate(hours):
        n = process_hour(hk, pr_fh, ai_shas, reverted_full, reverted_short, revert_prnums)
        if i % 24 == 0 or n == 0:
            pr_fh.flush()
            print(f"  {hk}: {n} events | ai_shas {len(ai_shas):,} | "
                  f"revertedSHAs {len(reverted_full)+len(reverted_short):,} | revPR# {len(revert_prnums):,}", flush=True)
    pr_fh.close()

    # build rows
    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    short = tuple(reverted_short)
    nrev = nai = total = 0
    with out.open("w", newline="") as f, pr_jsonl.open() as src:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for line in src:
            p = json.loads(line); total += 1
            repo = p["repo"]; num = p["pr"]; ms = p["merge_sha"]; hs = p["head_sha"]
            is_ai = 1 if (ms in ai_shas or hs in ai_shas) else 0
            rev = 1 if (ms and (ms in reverted_full or any(ms.startswith(s) for s in short))) \
                       or (repo, num) in revert_prnums else 0
            nai += is_ai; nrev += rev
            w.writerow({"repo": repo, "pr": num, "is_ai": is_ai, "reverted": rev,
                        "time_to_merge_h": p["time_to_merge_h"], "review_rounds": "",
                        "commits": p["commits"], "pr_size": p["pr_size"],
                        "files_changed": p["files_changed"], "comments": p["comments"],
                        "author_assoc": p["author_assoc"], "merged_at": p["merged_at"]})
    try: pr_jsonl.unlink()
    except OSError: pass
    print(f"\nPRs {total:,} | AI {nai:,} | reverted {nrev:,} -> {out}", flush=True)


if __name__ == "__main__":
    main()
