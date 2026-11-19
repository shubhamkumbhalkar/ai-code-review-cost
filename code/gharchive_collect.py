#!/usr/bin/env python3
"""Collect a real PR-level dataset from GH Archive (data.gharchive.org), the canonical real public
GitHub event dataset. Process-and-discard: each hour is downloaded, parsed into slim records, then
the raw file is deleted, so disk stays small.

Per contiguous window of hours we extract three real streams and join them:
  - merged PullRequestEvents -> all self-contained review-cost metrics + head/merge SHAs
  - PullRequestReviewEvents  -> real review submissions (latency, rounds)
  - PushEvent commits        -> strict AI-trailer detection (detect_ai_commits) -> AI-commit SHA set

AI flag per PR: the head SHA has assistant evidence no later than merge, or the merge SHA has evidence
within one hour after merge to allow GitHub event sequencing. The same timing rule defines linkability.
Formal-review metrics use deduplicated pre-merge reviews from non-author users whose type is not Bot.
All-review counts remain as diagnostics. Unlinkable PRs are excluded from analysis rather than silently
classified as non-AI.

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
COLS = [
    "repo", "pr", "is_ai", "ai_tools", "linkable", "author_type",
    "time_to_merge_h", "review_latency_h", "review_rounds",
    "all_review_latency_h", "all_review_rounds", "commits", "pr_size",
    "files_changed", "comments", "review_comments", "author_assoc",
    "created_at", "merged_at",
]
MERGE_PUSH_GRACE = dt.timedelta(hours=1)


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


def collect(hour_keys, analysis_hour_keys=None):
    analysis_hour_keys = set(hour_keys if analysis_hour_keys is None else analysis_hour_keys)
    prs = {}            # (repo,num) -> pr record
    reviews = {}        # (repo,num) -> review id -> review record
    ai_shas = {}        # commit SHA -> detected tools and earliest observation
    all_push_shas = {}  # commit SHA -> earliest observation
    hour_records = []
    for hk in hour_keys:
        url = f"https://data.gharchive.org/{hk}.json.gz"
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tf:
            path = tf.name
        try:
            result = subprocess.run(
                [
                    "curl", "-sfL", "--retry", "3", "--retry-delay", "2",
                    "-o", path, "--max-time", "300", url,
                ],
                capture_output=True,
                text=True,
            )
            size = os.path.getsize(path)
            if result.returncode != 0:
                raise RuntimeError(f"download failed for {hk}: {result.stderr.strip()}")
            if size < 1_000_000:
                raise RuntimeError(f"archive too small for {hk}: {size} bytes")
            event_count = 0
            with gzip.open(path) as archive:
                for line in archive:
                    try:
                        event = json.loads(line)
                    except Exception as error:
                        raise RuntimeError(
                            f"invalid JSON in {hk} at event {event_count + 1}: {error}"
                        ) from error
                    event_count += 1
                    event_type = event.get("type")
                    repo = event.get("repo", {}).get("name")
                    if event_type == "PullRequestEvent" and hk in analysis_hour_keys:
                        payload = event["payload"]
                        if payload.get("action") == "closed" and payload["pull_request"].get("merged"):
                            pull_request = payload["pull_request"]
                            key = (repo, pull_request["number"])
                            prs[key] = {
                                "repo": repo,
                                "pr": pull_request["number"],
                                "created_at": pull_request.get("created_at"),
                                "merged_at": pull_request.get("merged_at"),
                                "commits": pull_request.get("commits"),
                                "pr_size": pull_request.get("additions", 0) + pull_request.get("deletions", 0),
                                "files_changed": pull_request.get("changed_files"),
                                "comments": pull_request.get("comments"),
                                "review_comments": pull_request.get("review_comments"),
                                "author_assoc": pull_request.get("author_association"),
                                "author_login": (pull_request.get("user") or {}).get("login"),
                                "author_type": (pull_request.get("user") or {}).get("type"),
                                "head_sha": (pull_request.get("head") or {}).get("sha"),
                                "merge_sha": pull_request.get("merge_commit_sha"),
                            }
                    elif event_type == "PullRequestReviewEvent" and hk in analysis_hour_keys:
                        payload = event["payload"]
                        pull_request = payload.get("pull_request", {})
                        key = (repo, pull_request.get("number"))
                        review = payload.get("review") or {}
                        submitted = review.get("submitted_at")
                        if submitted:
                            reviewer = review.get("user") or {}
                            review_id = review.get("id") or review.get("node_id") or (
                                submitted,
                                reviewer.get("login"),
                                review.get("state"),
                            )
                            reviews.setdefault(key, {})[review_id] = {
                                "submitted_at": submitted,
                                "reviewer_login": reviewer.get("login"),
                                "reviewer_type": reviewer.get("type"),
                            }
                    elif event_type == "PushEvent":
                        payload = event["payload"]
                        event_time = dt.datetime.fromisoformat(
                            event["created_at"].replace("Z", "+00:00")
                        )
                        listed_shas = set()
                        for commit in payload.get("commits", []):
                            sha = commit.get("sha")
                            if sha:
                                listed_shas.add(sha)
                                observed = all_push_shas.get(sha)
                                if observed is None or event_time < observed:
                                    all_push_shas[sha] = event_time
                                tools = det.classify(commit.get("message") or "")
                                if tools:
                                    evidence = ai_shas.setdefault(
                                        sha, {"observed_at": event_time, "tools": set()}
                                    )
                                    evidence["observed_at"] = min(
                                        evidence["observed_at"], event_time
                                    )
                                    evidence["tools"].update(tools)
                        payload_head = payload.get("head")
                        if payload_head and payload_head not in listed_shas:
                            observed = all_push_shas.get(payload_head)
                            if observed is None or event_time < observed:
                                all_push_shas[payload_head] = event_time
            hour_records.append({"hour": hk, "bytes": size, "events": event_count})
            print(
                f"  {hk}: {event_count} events | merged PRs so far {len(prs)} | "
                f"AI shas {len(ai_shas)}",
                flush=True,
            )
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    return prs, reviews, ai_shas, all_push_shas, hour_records


def build_rows(prs, reviews, ai_shas, all_push_shas):
    rows = []
    for key, pull_request in prs.items():
        head_sha = pull_request.get("head_sha")
        merge_sha = pull_request.get("merge_sha")
        merged_at = pull_request.get("merged_at")
        merged_time = (
            dt.datetime.fromisoformat(merged_at.replace("Z", "+00:00")) if merged_at else None
        )

        def evidence_allowed(sha, evidence, grace=dt.timedelta(0)):
            if not sha or sha not in evidence:
                return False
            observed = evidence[sha]
            if isinstance(observed, dict):
                observed = observed["observed_at"]
            return merged_time is None or observed <= merged_time + grace

        linkable = evidence_allowed(head_sha, all_push_shas) or evidence_allowed(
            merge_sha, all_push_shas, MERGE_PUSH_GRACE
        )
        tools = sorted({
            tool
            for sha, grace in (
                (head_sha, dt.timedelta(0)),
                (merge_sha, MERGE_PUSH_GRACE),
            )
            if evidence_allowed(sha, ai_shas, grace)
            for tool in ai_shas[sha]["tools"]
        })
        created_time = dt.datetime.fromisoformat(
            pull_request["created_at"].replace("Z", "+00:00")
        )
        all_reviews = []
        for review in reviews.get(key, {}).values():
            submitted = dt.datetime.fromisoformat(
                review["submitted_at"].replace("Z", "+00:00")
            )
            if (merged_time is None or submitted <= merged_time) and submitted >= created_time:
                all_reviews.append((submitted, review))
        all_reviews.sort(key=lambda item: item[0])
        author_login = (pull_request.get("author_login") or "").casefold()
        human_reviews = [
            item
            for item in all_reviews
            if item[1].get("reviewer_login")
            and (item[1].get("reviewer_type") or "").casefold() != "bot"
            and item[1]["reviewer_login"].casefold() != author_login
        ]
        first_human_review = human_reviews[0][1]["submitted_at"] if human_reviews else None
        first_any_review = all_reviews[0][1]["submitted_at"] if all_reviews else None
        rows.append({
            "repo": pull_request["repo"],
            "pr": pull_request["pr"],
            "is_ai": int(bool(tools)),
            "ai_tools": ",".join(tools),
            "linkable": int(linkable),
            "author_type": pull_request.get("author_type") or "",
            "time_to_merge_h": _hours(pull_request["created_at"], merged_at),
            "review_latency_h": (
                _hours(pull_request["created_at"], first_human_review)
                if first_human_review else ""
            ),
            "review_rounds": len(human_reviews),
            "all_review_latency_h": (
                _hours(pull_request["created_at"], first_any_review) if first_any_review else ""
            ),
            "all_review_rounds": len(all_reviews),
            "commits": pull_request.get("commits"),
            "pr_size": pull_request.get("pr_size"),
            "files_changed": pull_request.get("files_changed"),
            "comments": pull_request.get("comments"),
            "review_comments": pull_request.get("review_comments"),
            "author_assoc": pull_request.get("author_assoc"),
            "created_at": pull_request.get("created_at"),
            "merged_at": merged_at,
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
    prs, reviews, ai_shas, all_push, _hour_records = collect(keys)
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
