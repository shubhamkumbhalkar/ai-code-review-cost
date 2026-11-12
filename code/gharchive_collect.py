#!/usr/bin/env python3
"""Collect a real PR-level dataset from GH Archive (data.gharchive.org), the canonical real public
GitHub event dataset. Process-and-discard: each hour is downloaded, parsed into slim records, then
the raw file is deleted, so disk stays small.

Per contiguous window of hours we extract three real streams and join them:
  - merged PullRequestEvents -> all self-contained review-cost metrics + head/merge SHAs
  - PullRequestReviewEvents  -> real review submissions (latency, rounds)
  - PushEvent commits        -> strict AI-trailer detection (detect_ai_commits) -> AI-commit SHA set

AI flag per PR: 1 if the PR's head_sha or merge_commit_sha is in the AI-commit SHA set.
Linkability is measured honestly: a PR is "linkable" only if its head_sha was seen in ANY push in
the window. PRs that are not linkable are reported and EXCLUDED from the AI-vs-non-AI comparison
(never silently defaulted to non-AI). This, and the contiguous-window containment, are stated as
limitations.

Usage: python3 code/gharchive_collect.py --start 2025-09-15-10 --hours 6 --out data/gha_val.csv
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import os
import subprocess
import tempfile
from pathlib import Path

import detect_ai_commits as det

ROOT = Path(__file__).resolve().parent.parent
COLS = ["repo", "pr", "is_ai", "linkable", "time_to_merge_h", "review_latency_h", "review_rounds",
        "commits", "pr_size", "files_changed", "comments", "review_comments", "author_assoc",
        "created_at", "merged_at"]


def _fmt(t):
    # GH Archive uses non-zero-padded hour
    return f"{t.year:04d}-{t.month:02d}-{t.day:02d}-{t.hour}"


def _hours(a, b):
    try:
        da = dt.datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = dt.datetime.fromisoformat(b.replace("Z", "+00:00"))
        return round((db - da).total_seconds() / 3600, 3)
    except Exception:
        return None


def collect(hour_keys):
    prs = {}            # (repo,num) -> pr record
    reviews = {}        # (repo,num) -> [submitted_at,...]
    ai_shas = set()
    all_push_shas = set()
    for hk in hour_keys:
        url = f"https://data.gharchive.org/{hk}.json.gz"
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tf:
            path = tf.name
        try:
            r = subprocess.run(["curl", "-s", "-o", path, "--max-time", "180", url],
                               capture_output=True)
            if r.returncode != 0 or os.path.getsize(path) < 1_000_000:
                print(f"  {hk}: download small/failed ({os.path.getsize(path)} B) - skip")
                continue
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
                            p = pl["pull_request"]; key = (repo, p["number"])
                            prs[key] = {
                                "repo": repo, "pr": p["number"],
                                "created_at": p.get("created_at"), "merged_at": p.get("merged_at"),
                                "commits": p.get("commits"), "pr_size": (p.get("additions", 0) + p.get("deletions", 0)),
                                "files_changed": p.get("changed_files"), "comments": p.get("comments"),
                                "review_comments": p.get("review_comments"),
                                "author_assoc": p.get("author_association"),
                                "head_sha": (p.get("head") or {}).get("sha"),
                                "merge_sha": p.get("merge_commit_sha"),
                            }
                    elif t == "PullRequestReviewEvent":
                        pl = e["payload"]; p = pl.get("pull_request", {})
                        key = (repo, p.get("number"))
                        sub = (pl.get("review") or {}).get("submitted_at")
                        if sub:
                            reviews.setdefault(key, []).append(sub)
                    elif t == "PushEvent":
                        for c in e["payload"].get("commits", []):
                            sha = c.get("sha")
                            if sha:
                                all_push_shas.add(sha)
                                if det.classify(c.get("message") or ""):
                                    ai_shas.add(sha)
                    n += 1
            print(f"  {hk}: {n} events | merged PRs so far {len(prs)} | AI shas {len(ai_shas)}")
        finally:
            try: os.remove(path)
            except OSError: pass
    return prs, reviews, ai_shas, all_push_shas


def build_rows(prs, reviews, ai_shas, all_push_shas):
    rows = []
    for key, p in prs.items():
        heads = [s for s in (p.get("head_sha"), p.get("merge_sha")) if s]
        linkable = any(s in all_push_shas for s in heads)
        is_ai = 1 if any(s in ai_shas for s in heads) else 0
        revs = sorted(reviews.get(key, []))
        lat = _hours(p["created_at"], revs[0]) if revs and p.get("created_at") else ""
        rows.append({
            "repo": p["repo"], "pr": p["pr"],
            "is_ai": is_ai, "linkable": int(linkable),
            "time_to_merge_h": _hours(p["created_at"], p["merged_at"]),
            "review_latency_h": lat, "review_rounds": len(revs),
            "commits": p.get("commits"), "pr_size": p.get("pr_size"),
            "files_changed": p.get("files_changed"), "comments": p.get("comments"),
            "review_comments": p.get("review_comments"), "author_assoc": p.get("author_assoc"),
            "created_at": p.get("created_at"), "merged_at": p.get("merged_at"),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="first hour, e.g. 2025-09-15-10")
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--out", default="data/gha_val.csv")
    args = ap.parse_args()
    keys = [_fmt(dt.datetime.strptime(args.start, "%Y-%m-%d-%H") + dt.timedelta(hours=i)) for i in range(args.hours)]
    print(f"window: {keys[0]} .. {keys[-1]} ({len(keys)} hours)")
    prs, reviews, ai_shas, all_push = collect(keys)
    rows = build_rows(prs, reviews, ai_shas, all_push)
    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader()
        for r in rows:
            w.writerow(r)
    link = sum(r["linkable"] for r in rows)
    ai = sum(r["is_ai"] for r in rows)
    print(f"\nmerged PRs: {len(rows)} | linkable (tip push seen): {link} ({100*link/max(len(rows),1):.1f}%) "
          f"| AI-flagged: {ai}")
    print(f"among linkable: AI {sum(1 for r in rows if r['linkable'] and r['is_ai'])} / "
          f"non-AI {sum(1 for r in rows if r['linkable'] and not r['is_ai'])}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
