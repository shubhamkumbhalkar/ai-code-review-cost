#!/usr/bin/env python3
"""Compare a deterministic sample of extracted review rows with GitHub's public API."""
import argparse
import datetime as dt
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

from detect_ai_commits import classify

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "gha_prs.csv"
DEFAULT_OUTPUT = ROOT / "data" / "validation_spotcheck.json"
DEFAULT_SUMMARY_OUTPUT = ROOT / "data" / "validation_spotcheck_summary.json"
FIELDS = (
    "merged_at", "commits", "pr_size", "files_changed", "comments",
    "review_comments", "author_type",
)
EXACT_FIELDS = (
    "merged_at", "commits", "pr_size", "files_changed", "author_type",
)
MONOTONIC_FIELDS = ("comments", "review_comments")


def extracted_values(row):
    return {
        "merged_at": row.merged_at,
        "commits": int(row.commits),
        "pr_size": int(row.pr_size),
        "files_changed": int(row.files_changed),
        "comments": int(row.comments),
        "review_comments": int(row.review_comments),
        "author_type": row.author_type,
    }


def api_values(pull_request):
    return {
        "merged_at": pull_request.get("merged_at"),
        "commits": pull_request.get("commits"),
        "pr_size": (pull_request.get("additions") or 0) + (pull_request.get("deletions") or 0),
        "files_changed": pull_request.get("changed_files"),
        "comments": pull_request.get("comments"),
        "review_comments": pull_request.get("review_comments"),
        "author_type": (pull_request.get("user") or {}).get("type"),
    }


def compare(expected, actual):
    differences = {
        name: {"expected": expected[name], "actual": actual[name]}
        for name in EXACT_FIELDS
        if expected[name] != actual[name]
    }
    differences.update({
        name: {"expected_minimum": expected[name], "actual": actual[name]}
        for name in MONOTONIC_FIELDS
        if actual[name] is None or actual[name] < expected[name]
    })
    return differences


def post_merge_increases(expected, actual):
    return {
        name: {
            "merge_time": expected[name],
            "current": actual[name],
            "increase": actual[name] - expected[name],
        }
        for name in MONOTONIC_FIELDS
        if actual[name] is not None and actual[name] > expected[name]
    }


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_input_manifest(input_path):
    input_path = Path(input_path)
    manifest_path = input_path.with_suffix(input_path.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") != "complete":
        raise ValueError("spot-check input manifest is not complete")
    if manifest.get("requested_hours") != manifest.get("completed_hours"):
        raise ValueError("spot-check input manifest reports incomplete hours")
    if manifest.get("csv_bytes") != input_path.stat().st_size:
        raise ValueError("spot-check input byte size does not match manifest")
    digest = file_sha256(input_path)
    if manifest.get("csv_sha256") != digest:
        raise ValueError("spot-check input SHA-256 does not match manifest")
    return {
        "manifest_path": manifest_path.name,
        "requested_hours": manifest.get("requested_hours"),
        "completed_hours": manifest.get("completed_hours"),
        "csv_bytes": input_path.stat().st_size,
        "csv_sha256": digest,
    }


def fixed_coordinate_samples(frame, coordinates_path, targets):
    """Resolve explicit coordinates and require exact final labels and group sizes."""
    path = Path(coordinates_path)
    coordinates = json.loads(path.read_text())
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("spot-check coordinates must be a nonempty JSON list")
    keys = set()
    selected = []
    for item in coordinates:
        if not isinstance(item, dict) or not {"repo", "pr", "is_ai"} <= set(item):
            raise ValueError("each coordinate requires repo, pr, and is_ai")
        key = (str(item["repo"]), int(item["pr"]))
        if key in keys:
            raise ValueError(f"duplicate spot-check coordinate: {key[0]}#{key[1]}")
        keys.add(key)
        rows = frame[(frame.repo == key[0]) & (frame.pr == key[1])]
        if len(rows) != 1:
            raise ValueError(
                f"spot-check coordinate must match one final row: {key[0]}#{key[1]}"
            )
        row = rows.iloc[0]
        if int(row.is_ai) != int(item["is_ai"]):
            raise ValueError(
                f"spot-check label mismatch for {key[0]}#{key[1]}: "
                f"expected {item['is_ai']}, final {int(row.is_ai)}"
            )
        selected.append(row)
    selected_frame = pd.DataFrame(selected)
    samples = []
    for group, group_target in targets.items():
        group_rows = selected_frame[selected_frame.is_ai == group]
        if len(group_rows) != group_target:
            raise ValueError(
                f"fixed coordinates require exactly {group_target} rows for group {group}; "
                f"found {len(group_rows)}"
            )
        samples.append((group, group_target, group_rows))
    return samples, {
        "mode": "fixed_coordinates",
        "coordinates_path": path.name,
        "coordinates_sha256": file_sha256(path),
    }


def api_commit_tools(commits):
    tools = set()
    for item in commits:
        message = ((item.get("commit") or {}).get("message")) or ""
        tools.update(classify(message))
    return sorted(tools)


def is_rate_limited(response):
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    try:
        message = str(response.json().get("message", ""))
    except (ValueError, AttributeError):
        message = response.text
    return "rate limit" in message.casefold()


def aggregate_result(result):
    increases = Counter()
    increase_amounts = Counter()
    expected_tools = Counter()
    detected_tools = Counter()
    for row in result["checked"]:
        for field, details in row["post_merge_increases"].items():
            increases[field] += 1
            increase_amounts[field] += details["increase"]
        label = row["label_validation"]
        if label:
            expected_tools.update(label["expected_tools"])
            detected_tools.update(label["detected_tools"])
    return {
        "accessed_utc": result["accessed_utc"],
        "target": result["target"],
        "input_manifest": result["input_manifest"],
        "selection": {
            "mode": result["selection"]["mode"],
            "coordinates_sha256": result["selection"].get("coordinates_sha256"),
            "description": (
                "Balanced validation fixture selected from the final strict CSV for "
                "continued public availability and bounded commit-message checks; "
                "not an inferential random sample."
            ),
        },
        "target_by_group": result["target_by_group"],
        "immutable_fields": list(EXACT_FIELDS),
        "merge_time_snapshot_fields": list(MONOTONIC_FIELDS),
        "checked_count": len(result["checked"]),
        "unavailable_count": len(result["unavailable"]),
        "records_with_mismatches": sum(
            bool(row["mismatches"]) for row in result["checked"]
        ),
        "records_with_post_merge_increases": sum(
            bool(row["post_merge_increases"]) for row in result["checked"]
        ),
        "post_merge_increases_by_field": dict(sorted(increases.items())),
        "post_merge_increase_amounts_by_field": dict(
            sorted(increase_amounts.items())
        ),
        "tagged_label_expected_counts": dict(sorted(expected_tools.items())),
        "tagged_label_detected_counts": dict(sorted(detected_tools.items())),
        "all_checked_fields_match": result["all_checked_fields_match"],
        "all_tagged_labels_match": result["all_tagged_labels_match"],
    }


def run(
    input_path, output_path, seed, target, candidates,
    coordinates_path=None, summary_output=None,
):
    input_manifest = validate_input_manifest(input_path)
    frame = pd.read_csv(
        input_path, dtype={"ai_tools": "string"}, low_memory=False
    )
    frame = frame[
        (pd.to_numeric(frame["linkable"], errors="coerce").fillna(0) == 1)
        & (frame["author_type"].fillna("").str.casefold() == "user")
    ]
    targets = {1: target // 2, 0: target - target // 2}
    if coordinates_path:
        samples, selection = fixed_coordinate_samples(
            frame, coordinates_path, targets
        )
    else:
        samples = []
        for group, group_target in targets.items():
            group_rows = frame[frame.is_ai == group]
            candidate_count = min(len(group_rows), max(candidates, group_target))
            if candidate_count < group_target:
                raise ValueError(f"group {group} has only {candidate_count} candidates")
            samples.append(
                (
                    group,
                    group_target,
                    group_rows.sample(n=candidate_count, random_state=seed + group),
                )
            )
        selection = {
            "mode": "seeded_random_candidates",
            "seed": seed,
            "candidates_per_group": candidates,
        }
    session = requests.Session()
    session.headers["User-Agent"] = "ai-code-review-cost-validation"
    checked = []
    unavailable = []
    for group, group_target, sample in samples:
        completed = 0
        for row in sample.itertuples(index=False):
            if completed >= group_target:
                break
            url = f"https://api.github.com/repos/{row.repo}/pulls/{int(row.pr)}"
            response = session.get(url, timeout=30)
            if is_rate_limited(response):
                raise RuntimeError(
                    f"GitHub API rate limited the validation: {response.status_code}"
                )
            if response.status_code != 200:
                unavailable.append({
                    "repo": row.repo,
                    "pr": int(row.pr),
                    "is_ai": group,
                    "status": response.status_code,
                })
                continue
            pull_payload = response.json()
            expected = extracted_values(row)
            actual = api_values(pull_payload)
            mismatches = compare(expected, actual)
            label_validation = None
            if group == 1:
                commits_response = session.get(
                    f"{url}/commits", params={"per_page": 100}, timeout=30
                )
                if is_rate_limited(commits_response):
                    raise RuntimeError(
                        "GitHub API rate limited commit-message validation: "
                        f"{commits_response.status_code}"
                    )
                expected_tools = sorted(filter(None, str(row.ai_tools).split(",")))
                detected_tools = []
                evidence_sources = []
                statuses = {"pull_request_commits": commits_response.status_code}
                if commits_response.status_code == 200:
                    detected_tools = api_commit_tools(commits_response.json())
                    if detected_tools:
                        evidence_sources.append("pull_request_commits")
                label_match = set(expected_tools) <= set(detected_tools)
                merge_sha = pull_payload.get("merge_commit_sha")
                if not label_match and merge_sha:
                    merge_response = session.get(
                        f"https://api.github.com/repos/{row.repo}/commits/{merge_sha}",
                        timeout=30,
                    )
                    if is_rate_limited(merge_response):
                        raise RuntimeError(
                            "GitHub API rate limited merge-commit validation: "
                            f"{merge_response.status_code}"
                        )
                    statuses["merge_commit"] = merge_response.status_code
                    if merge_response.status_code == 200:
                        merge_message = (
                            (merge_response.json().get("commit") or {}).get("message")
                            or ""
                        )
                        merge_tools = classify(merge_message)
                        if merge_tools:
                            evidence_sources.append("merge_commit")
                            detected_tools = sorted(set(detected_tools) | merge_tools)
                    label_match = set(expected_tools) <= set(detected_tools)
                    time.sleep(0.15)
                label_validation = {
                    "expected_tools": expected_tools,
                    "detected_tools": detected_tools,
                    "evidence_sources": evidence_sources,
                    "statuses": statuses,
                    "match": label_match,
                }
                if not label_match:
                    mismatches["ai_tools"] = label_validation
                time.sleep(0.15)
            checked.append({
                "repo": row.repo,
                "pr": int(row.pr),
                "is_ai": group,
                "url": url,
                "mismatches": mismatches,
                "post_merge_increases": post_merge_increases(expected, actual),
                "label_validation": label_validation,
            })
            completed += 1
            time.sleep(0.15)
        if completed != group_target:
            raise RuntimeError(
                f"only {completed} of {group_target} requested records were available for group {group}"
            )
    result = {
        "accessed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "target": target,
        "input_manifest": input_manifest,
        "selection": selection,
        "target_by_group": {"ai": targets[1], "non_ai": targets[0]},
        "checked": checked,
        "unavailable": unavailable,
        "all_checked_fields_match": len(checked) == target and all(
            not row["mismatches"] for row in checked
        ),
        "all_tagged_labels_match": all(
            row["label_validation"] is None or row["label_validation"]["match"]
            for row in checked
        ),
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    if summary_output:
        Path(summary_output).write_text(
            json.dumps(aggregate_result(result), indent=2, allow_nan=False) + "\n"
        )
    if not result["all_checked_fields_match"]:
        raise RuntimeError("one or more extracted records differ from the GitHub API")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=20261118)
    parser.add_argument("--target", type=int, default=10)
    parser.add_argument("--candidates", type=int, default=35)
    parser.add_argument(
        "--coordinates",
        help="JSON list of exact repo, PR, and final is_ai coordinates",
    )
    parser.add_argument(
        "--summary-output", default=str(DEFAULT_SUMMARY_OUTPUT),
        help="redacted aggregate result without repository or pull-request identifiers",
    )
    args = parser.parse_args()
    result = run(
        args.input, args.output, args.seed, args.target, args.candidates,
        args.coordinates, args.summary_output,
    )
    print(f"matched {len(result['checked'])} records; unavailable {len(result['unavailable'])}")


if __name__ == "__main__":
    main()
