#!/usr/bin/env python3
"""Collect merged pull requests and conservative downstream revert signals from GH Archive.

The collector scans a contiguous UTC window in reverse hour order. Reverse scanning lets a
pre-merge push be matched after its pull request target is known without retaining every public
commit SHA. Within each downloaded hour, pull requests are processed before pushes so event order
inside that hour cannot hide a match.

AI label: the head SHA has assistant evidence no later than merge, or the merge SHA has evidence within
one hour after merge to allow GitHub event sequencing. The same timing rule defines linkability. Tool
names and pull-request author type are retained so analysis can report composition and exclude bots.
Revert: a later same-repository commit explicitly says ``This reverts commit <sha>``, or a merged
revert pull request body explicitly says it reverts a pull request in the same repository. The earliest
evidence timestamp is retained so analysis can apply one fixed follow-up period to every pull request.

The output is replaced atomically only after every requested hour downloads and parses successfully.
A sibling ``.manifest.json`` file records every hour and the resulting counts.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import detect_ai_commits as det

ROOT = Path(__file__).resolve().parent.parent
REV_SHA = re.compile(r"this reverts commit ([0-9a-f]{7,40})", re.I)
REV_PR_URL = re.compile(
    r"\breverts?\s+https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d{1,7})\b",
    re.I,
)
REV_PR_SCOPED = re.compile(
    r"\breverts?\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d{1,7})\b",
    re.I,
)
REV_PR_LOCAL = re.compile(r"\breverts?\s+#(\d{1,7})\b", re.I)
COLS = [
    "repo", "pr", "base_ref", "is_ai", "ai_tools", "linkable", "author_type", "reverted",
    "reverted_at", "time_to_merge_h", "review_rounds",
    "commits", "pr_size", "files_changed", "comments", "author_assoc", "merged_at",
]
MIN_ARCHIVE_BYTES = 1_000_000
MERGE_PUSH_GRACE = dt.timedelta(hours=1)


def _repo_key(repo):
    return (repo or "").casefold()


def extract_revert_pr_refs(repo, current_pr, title, body):
    """Extract explicit references to reverted PRs from a revert PR body."""
    if not (title or "").strip().casefold().startswith("revert"):
        return set()
    repo_key = _repo_key(repo)
    text = body or ""
    refs = set()
    for owner, name, number in REV_PR_URL.findall(text):
        target_repo = _repo_key(f"{owner}/{name}")
        if target_repo == repo_key and int(number) != int(current_pr):
            refs.add((repo_key, int(number)))
    for target, number in REV_PR_SCOPED.findall(text):
        target_repo = _repo_key(target)
        if target_repo == repo_key and int(number) != int(current_pr):
            refs.add((repo_key, int(number)))
    for number in REV_PR_LOCAL.findall(text):
        if int(number) != int(current_pr):
            refs.add((repo_key, int(number)))
    return refs


def _record_earliest(mapping, key, observed_at):
    previous = mapping.get(key)
    if previous is None or observed_at < previous:
        mapping[key] = observed_at


def record_revert_shas(repo, ref, message, observed_at, reverted_full, reverted_short):
    """Record timestamped git-revert targets, scoped to repository and branch."""
    repo_key = _repo_key(repo)
    for match in REV_SHA.finditer(message or ""):
        sha = match.group(1).lower()
        if len(sha) == 40:
            _record_earliest(reverted_full, (repo_key, sha, ref), observed_at)
        else:
            bucket = reverted_short.setdefault((repo_key, ref), {})
            _record_earliest(bucket, sha, observed_at)


def revert_timestamp(
    repo, base_ref, merge_sha, pr_number, merged_at, reverted_full, reverted_short, revert_prnums
):
    """Return earliest revert evidence on the original target branch after merge."""
    repo_key = _repo_key(repo)
    sha = (merge_sha or "").lower()
    branch_ref = f"refs/heads/{base_ref}" if base_ref else ""
    merge_time = dt.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    candidates = []
    full_time = reverted_full.get((repo_key, sha, branch_ref)) if sha and branch_ref else None
    if full_time is not None:
        candidates.append(full_time)
    if sha and branch_ref:
        bucket = reverted_short.get((repo_key, branch_ref), {})
        candidates.extend(
            observed_at
            for length in range(7, min(len(sha), 40))
            if (observed_at := bucket.get(sha[:length])) is not None
        )
    pr_time = revert_prnums.get((repo_key, int(pr_number), base_ref))
    if pr_time is not None:
        candidates.append(pr_time)
    valid = [observed_at for observed_at in candidates if observed_at >= merge_time]
    return min(valid) if valid else None


def _observed_by(evidence, sha, cutoff):
    if not sha:
        return False
    if hasattr(evidence, "get"):
        observed = evidence.get(sha)
        if isinstance(observed, dict):
            observed = observed["observed_at"]
        return observed is not None and (cutoff is None or observed <= cutoff)
    return sha in evidence


def classify_pull_request(head_sha, merge_sha, linked_shas, ai_shas, merged_at=None):
    """Return linkable and AI flags using bounded event-sequencing cutoffs."""
    cutoff = None
    if merged_at:
        cutoff = dt.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    head_linked = _observed_by(linked_shas, (head_sha or "").lower(), cutoff)
    merge_cutoff = cutoff + MERGE_PUSH_GRACE if cutoff is not None else None
    merge_linked = _observed_by(linked_shas, (merge_sha or "").lower(), merge_cutoff)
    head_ai = _observed_by(ai_shas, (head_sha or "").lower(), cutoff)
    merge_ai = _observed_by(ai_shas, (merge_sha or "").lower(), merge_cutoff)
    linkable = head_linked or merge_linked
    return int(linkable), int(linkable and (head_ai or merge_ai))


def assistant_tools(head_sha, merge_sha, ai_shas, merged_at):
    cutoff = dt.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    tools = set()
    for sha, evidence_cutoff in (
        ((head_sha or "").lower(), cutoff),
        ((merge_sha or "").lower(), cutoff + MERGE_PUSH_GRACE),
    ):
        evidence = ai_shas.get(sha)
        if evidence and evidence["observed_at"] <= evidence_cutoff:
            tools.update(evidence["tools"])
    return sorted(tools)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path, payload):
    path = Path(path)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix=f"{path.name}.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _fmt(value):
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}-{value.hour}"


def _hours(start, end):
    try:
        first = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        second = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
        return round((second - first).total_seconds() / 3600, 3)
    except (AttributeError, TypeError, ValueError):
        return None


def download_hour(hour_key):
    """Download one hour and fail if curl, size, gzip, or JSON validation fails."""
    url = f"https://data.gharchive.org/{hour_key}.json.gz"
    with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as handle:
        path = Path(handle.name)
    command = [
        "curl", "-sfL", "--retry", "3", "--retry-delay", "2", "--max-time", "300",
        "-o", str(path), url,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"download failed for {hour_key}: {result.stderr.strip()}")
    size = path.stat().st_size
    if size < MIN_ARCHIVE_BYTES:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"archive too small for {hour_key}: {size} bytes")
    try:
        with gzip.open(path, "rb") as archive:
            first = archive.readline()
            if not first:
                raise RuntimeError("archive contains no events")
            json.loads(first)
    except Exception as error:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"invalid archive for {hour_key}: {error}") from error
    return path, size


def _pull_request_record(repo, pull_request):
    additions = pull_request.get("additions")
    deletions = pull_request.get("deletions")
    pr_size = (
        additions + deletions
        if isinstance(additions, int) and isinstance(deletions, int)
        else None
    )
    return {
        "repo": repo,
        "pr": pull_request["number"],
        "base_ref": ((pull_request.get("base") or {}).get("ref") or ""),
        "merge_sha": (pull_request.get("merge_commit_sha") or "").lower(),
        "head_sha": ((pull_request.get("head") or {}).get("sha") or "").lower(),
        "time_to_merge_h": _hours(pull_request.get("created_at"), pull_request.get("merged_at")),
        "commits": pull_request.get("commits"),
        "pr_size": pr_size,
        "files_changed": pull_request.get("changed_files"),
        "comments": pull_request.get("comments"),
        "author_assoc": pull_request.get("author_association"),
        "author_type": (pull_request.get("user") or {}).get("type"),
        "merged_at": pull_request.get("merged_at"),
    }


def process_hour(
    hour_key,
    pr_handle,
    target_shas,
    linked_shas,
    ai_shas,
    reverted_full,
    reverted_short,
    revert_prnums,
    seen_prs,
    future_pushes,
):
    """Parse one hour once, then process PRs before buffered push commits."""
    path, size = download_hour(hour_key)
    events = 0
    merged_prs = 0
    pull_events = []
    push_commits = []
    try:
        with gzip.open(path, "rb") as archive:
            for line in archive:
                event = json.loads(line)
                events += 1
                event_type = event.get("type")
                repo = event.get("repo", {}).get("name")
                if event_type == "PullRequestEvent":
                    pull_events.append((repo, event.get("payload", {})))
                elif event_type == "PushEvent":
                    payload = event.get("payload", {})
                    ref = payload.get("ref") or ""
                    event_time = dt.datetime.fromisoformat(
                        event["created_at"].replace("Z", "+00:00")
                    )
                    listed_shas = set()
                    for commit in payload.get("commits", []):
                        sha = (commit.get("sha") or "").lower()
                        if sha:
                            listed_shas.add(sha)
                        push_commits.append(
                            (repo, ref, event_time, sha, commit.get("message") or "")
                        )
                    payload_head = (payload.get("head") or "").lower()
                    if payload_head and payload_head not in listed_shas:
                        push_commits.append((repo, ref, event_time, payload_head, ""))

        for repo, payload in pull_events:
            pull_request = payload.get("pull_request", {})
            if payload.get("action") != "closed" or not pull_request.get("merged"):
                continue
            key = (_repo_key(repo), int(pull_request["number"]))
            if key in seen_prs:
                continue
            seen_prs.add(key)
            record = _pull_request_record(repo, pull_request)
            pr_handle.write(json.dumps(record) + "\n")
            merged_prs += 1
            merge_time = dt.datetime.fromisoformat(
                record["merged_at"].replace("Z", "+00:00")
            )
            future = future_pushes.get(record["merge_sha"])
            if future and future["observed_at"] <= merge_time + MERGE_PUSH_GRACE:
                linked_shas[record["merge_sha"]] = future["observed_at"]
                if future["tools"]:
                    ai_shas[record["merge_sha"]] = {
                        "observed_at": future["observed_at"],
                        "tools": set(future["tools"]),
                    }
            for target in extract_revert_pr_refs(
                repo,
                pull_request.get("number", 0),
                pull_request.get("title"),
                (pull_request.get("body") or "")[:2000],
            ):
                _record_earliest(
                    revert_prnums, target + (record["base_ref"],), merge_time
                )
            for sha in (record["head_sha"], record["merge_sha"]):
                if not sha or sha in linked_shas:
                    continue
                previous = target_shas.get(sha)
                if previous is None or merge_time < previous:
                    target_shas[sha] = merge_time

        current_pushes = {}
        for repo, ref, event_time, sha, message in push_commits:
            tools = det.classify(message)
            if sha:
                cached = current_pushes.setdefault(
                    sha, {"observed_at": event_time, "tools": set()}
                )
                cached["observed_at"] = min(cached["observed_at"], event_time)
                cached["tools"].update(tools)
            if sha and (sha in target_shas or sha in linked_shas):
                previous = linked_shas.get(sha)
                if previous is None or event_time < previous:
                    linked_shas[sha] = event_time
                if tools:
                    evidence = ai_shas.setdefault(
                        sha, {"observed_at": event_time, "tools": set()}
                    )
                    evidence["observed_at"] = min(evidence["observed_at"], event_time)
                    evidence["tools"].update(tools)
                target_shas.pop(sha, None)
            record_revert_shas(
                repo, ref, message, event_time, reverted_full, reverted_short
            )
    finally:
        path.unlink(missing_ok=True)
    return (
        {"hour": hour_key, "bytes": size, "events": events, "merged_prs": merged_prs},
        current_pushes,
    )


def collect(start, days, output):
    """Collect the full window and atomically replace output after complete validation."""
    start_utc = dt.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    hours = [_fmt(start_utc + dt.timedelta(hours=index)) for index in range(days * 24)]
    output = Path(output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")

    target_shas = {}
    linked_shas = {}
    ai_shas = {}
    reverted_full = {}
    reverted_short = {}
    revert_prnums = {}
    seen_prs = set()
    hour_records = []
    future_pushes = {}

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", prefix="gha_reverts_", dir=output.parent, delete=False
    ) as pr_handle:
        pr_path = Path(pr_handle.name)
        try:
            for index, hour_key in enumerate(reversed(hours), start=1):
                record, future_pushes = process_hour(
                    hour_key,
                    pr_handle,
                    target_shas,
                    linked_shas,
                    ai_shas,
                    reverted_full,
                    reverted_short,
                    revert_prnums,
                    seen_prs,
                    future_pushes,
                )
                hour_records.append(record)
                if index == 1 or index % 24 == 0:
                    pr_handle.flush()
                    print(
                        f"  {index:3d}/{len(hours)} {hour_key}: {record['events']:,} events | "
                        f"PRs {len(seen_prs):,} | linked SHAs {len(linked_shas):,} | "
                        f"AI SHAs {len(ai_shas):,}",
                        flush=True,
                    )
        except Exception:
            pr_path.unlink(missing_ok=True)
            raise

    temp_output = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", suffix=".csv", prefix="gha_reverts_", dir=output.parent, delete=False
        ) as csv_handle:
            temp_output = Path(csv_handle.name)
            writer = csv.DictWriter(csv_handle, fieldnames=COLS)
            writer.writeheader()
            total = linkable_total = ai_total = reverted_total = linkable_reverted = 0
            bot_authored_total = 0
            tool_counts = defaultdict(int)
            with pr_path.open() as source:
                for line in source:
                    pull_request = json.loads(line)
                    total += 1
                    linkable, is_ai = classify_pull_request(
                        pull_request["head_sha"],
                        pull_request["merge_sha"],
                        linked_shas,
                        ai_shas,
                        pull_request["merged_at"],
                    )
                    tools = assistant_tools(
                        pull_request["head_sha"],
                        pull_request["merge_sha"],
                        ai_shas,
                        pull_request["merged_at"],
                    )
                    reverted_at = revert_timestamp(
                        pull_request["repo"],
                        pull_request["base_ref"],
                        pull_request["merge_sha"],
                        pull_request["pr"],
                        pull_request["merged_at"],
                        reverted_full,
                        reverted_short,
                        revert_prnums,
                    )
                    reverted = int(reverted_at is not None)
                    linkable_total += linkable
                    ai_total += is_ai
                    if linkable and (pull_request["author_type"] or "").casefold() == "bot":
                        bot_authored_total += 1
                    if linkable:
                        for tool in tools:
                            tool_counts[tool] += 1
                    reverted_total += reverted
                    linkable_reverted += reverted if linkable else 0
                    writer.writerow(
                        {
                            "repo": pull_request["repo"],
                            "pr": pull_request["pr"],
                            "base_ref": pull_request["base_ref"],
                            "is_ai": is_ai,
                            "ai_tools": ",".join(tools),
                            "linkable": linkable,
                            "author_type": pull_request["author_type"] or "",
                            "reverted": reverted,
                            "reverted_at": (
                                reverted_at.isoformat().replace("+00:00", "Z") if reverted_at else ""
                            ),
                            "time_to_merge_h": pull_request["time_to_merge_h"],
                            "review_rounds": "",
                            "commits": pull_request["commits"],
                            "pr_size": pull_request["pr_size"],
                            "files_changed": pull_request["files_changed"],
                            "comments": pull_request["comments"],
                            "author_assoc": pull_request["author_assoc"],
                            "merged_at": pull_request["merged_at"],
                        }
                    )

    except Exception:
        pr_path.unlink(missing_ok=True)
        if temp_output is not None:
            temp_output.unlink(missing_ok=True)
        raise

    pr_path.unlink(missing_ok=True)
    csv_bytes = temp_output.stat().st_size
    csv_sha256 = file_sha256(temp_output)
    try:
        os.replace(temp_output, output)
    except Exception:
        temp_output.unlink(missing_ok=True)
        raise
    manifest = {
        "status": "complete",
        "start_utc": start_utc.isoformat(),
        "days": days,
        "window_end_exclusive_utc": (start_utc + dt.timedelta(days=days)).isoformat(),
        "requested_hours": len(hours),
        "completed_hours": len(hour_records),
        "csv_bytes": csv_bytes,
        "csv_sha256": csv_sha256,
        "hours": list(reversed(hour_records)),
        "pull_requests": total,
        "linkable_pull_requests": linkable_total,
        "ai_pull_requests": ai_total,
        "ai_tools": dict(sorted(tool_counts.items())),
        "bot_authored_linkable_pull_requests": bot_authored_total,
        "reverted_pull_requests": reverted_total,
        "linkable_reverted_pull_requests": linkable_reverted,
        "matching": {
            "revert_full_shas": len(reverted_full),
            "revert_short_shas": sum(
                len(prefixes) for prefixes in reverted_short.values()
            ),
            "revert_pr_numbers": len(revert_prnums),
            "linked_shas": len(linked_shas),
            "unmatched_target_shas": len(target_shas),
        },
    }
    _write_json_atomic(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="UTC start date, YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default="data/gha_reverts.csv")
    args = parser.parse_args()
    if args.days < 15:
        parser.error("--days must be at least 15 to permit a 14-day revert follow-up")
    print(
        f"contiguous UTC window: {args.start} for {args.days} days, processed newest hour first",
        flush=True,
    )
    manifest = collect(args.start, args.days, args.out)
    print(
        f"complete: {manifest['pull_requests']:,} PRs | "
        f"linkable {manifest['linkable_pull_requests']:,} | "
        f"AI {manifest['ai_pull_requests']:,} | "
        f"reverted {manifest['reverted_pull_requests']:,}",
        flush=True,
    )


if __name__ == "__main__":
    main()
