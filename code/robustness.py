#!/usr/bin/env python3
"""Analyze conservative revert signals with censoring and repository-aware sensitivity checks."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from analyze import bh

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "gha_reverts.csv"
DEFAULT_REPORT = ROOT / "data" / "robustness.txt"
DEFAULT_SUMMARY = ROOT / "data" / "robustness_summary.json"
SIZE_BANDS = ("small<=50", "med51-300", "large>300", "unknown")


def size_band(value):
    if value is None:
        return "unknown"
    if value <= 50:
        return "small<=50"
    if value <= 300:
        return "med51-300"
    return "large>300"



def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(input_path, manifest_path, start, days):
    manifest = json.loads(Path(manifest_path).read_text())
    expected_end = start + dt.timedelta(days=days)
    if manifest.get("status") != "complete":
        raise ValueError("collector manifest is not complete")
    if manifest.get("requested_hours") != manifest.get("completed_hours"):
        raise ValueError("collector manifest reports incomplete hours")
    if manifest.get("start_utc") != start.isoformat():
        raise ValueError("analysis start does not match collector manifest")
    if manifest.get("window_end_exclusive_utc") != expected_end.isoformat():
        raise ValueError("analysis end does not match collector manifest")
    input_path = Path(input_path)
    if manifest.get("csv_bytes") != input_path.stat().st_size:
        raise ValueError("input byte size does not match collector manifest")
    if manifest.get("csv_sha256") != file_sha256(input_path):
        raise ValueError("input SHA-256 does not match collector manifest")
    return manifest

def two_proportion(reverts_ai, count_ai, reverts_non, count_non):
    """Return rates, difference, Newcombe interval, pooled z statistic, and p-value."""
    if count_ai == 0 or count_non == 0:
        return (math.nan,) * 7
    rate_ai = reverts_ai / count_ai
    rate_non = reverts_non / count_non
    difference = rate_ai - rate_non
    ai_low, ai_high = wilson_interval(reverts_ai, count_ai)
    non_low, non_high = wilson_interval(reverts_non, count_non)
    interval_low = difference - math.sqrt(
        (rate_ai - ai_low) ** 2 + (non_high - rate_non) ** 2
    )
    interval_high = difference + math.sqrt(
        (ai_high - rate_ai) ** 2 + (rate_non - non_low) ** 2
    )
    pooled = (reverts_ai + reverts_non) / (count_ai + count_non)
    null_se = math.sqrt(pooled * (1 - pooled) * (1 / count_ai + 1 / count_non))
    z_value = difference / null_se if null_se else 0.0
    p_value = math.erfc(abs(z_value) / math.sqrt(2))
    return (
        rate_ai * 100,
        rate_non * 100,
        difference * 100,
        interval_low * 100,
        interval_high * 100,
        z_value,
        p_value,
    )



def wilson_interval(successes, total, z_value=1.96):
    if total == 0:
        return math.nan, math.nan
    proportion = successes / total
    denominator = 1 + z_value**2 / total
    center = (proportion + z_value**2 / (2 * total)) / denominator
    half_width = (
        z_value
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z_value**2 / (4 * total**2)
        )
        / denominator
    )
    return center - half_width, center + half_width

def exact_binomial_sign_p(higher, lower):
    """Two-sided exact binomial sign-test p-value after excluding ties."""
    total = higher + lower
    if total == 0:
        return 1.0
    extreme = min(higher, lower)
    lower_tail = sum(math.comb(total, value) for value in range(extreme + 1)) / (2**total)
    return min(1.0, 2 * lower_tail)


def permutation_result(reverts_ai, count_ai, total_reverts, total, draws, seed):
    """Hypergeometric random-label sensitivity check with deterministic Monte Carlo draws."""
    rng = np.random.default_rng(seed)
    simulations = rng.hypergeometric(
        total_reverts,
        total - total_reverts,
        count_ai,
        size=draws,
    )
    expected = count_ai * total_reverts / total
    observed_distance = abs(reverts_ai - expected)
    p_value = (
        np.count_nonzero(np.abs(simulations - expected) >= observed_distance) + 1
    ) / (draws + 1)
    standard_deviation = float(simulations.std(ddof=1))
    return {
        "draws": draws,
        "seed": seed,
        "observed_ai_reverts": reverts_ai,
        "expected_ai_reverts": expected,
        "standard_deviation": standard_deviation,
        "standard_deviations_from_null": (
            (reverts_ai - expected) / standard_deviation if standard_deviation else 0.0
        ),
        "two_sided_p": float(p_value),
    }


def cluster_robust_difference(by_repository):
    """Return a repository-clustered sandwich interval for the rate difference."""
    rows = np.asarray(
        [
            [values[1][0], values[1][1], values[0][0], values[0][1]]
            for values in by_repository.values()
        ],
        dtype=np.float64,
    )
    if len(rows) < 2:
        raise RuntimeError("cluster-robust inference requires at least two repositories")
    ai_count = rows[:, 0].sum()
    ai_reverts = rows[:, 1].sum()
    non_count = rows[:, 2].sum()
    non_reverts = rows[:, 3].sum()
    if not ai_count or not non_count:
        raise RuntimeError("cluster-robust inference requires both comparison groups")
    ai_rate = ai_reverts / ai_count
    non_rate = non_reverts / non_count
    difference = ai_rate - non_rate
    cluster_scores = (
        (rows[:, 1] - ai_rate * rows[:, 0]) / ai_count
        - (rows[:, 3] - non_rate * rows[:, 2]) / non_count
    )
    variance = len(rows) / (len(rows) - 1) * float(cluster_scores @ cluster_scores)
    standard_error = math.sqrt(max(variance, 0.0))
    z_value = difference / standard_error if standard_error else 0.0
    p_value = math.erfc(abs(z_value) / math.sqrt(2))
    return {
        "method": "repository-clustered sandwich interval",
        "repositories": int(len(rows)),
        "difference_pp": float(100 * difference),
        "standard_error_pp": float(100 * standard_error),
        "ci_95_pp": [
            float(100 * (difference - 1.96 * standard_error)),
            float(100 * (difference + 1.96 * standard_error)),
        ],
        "z": float(z_value),
        "p": float(p_value),
    }


def _new_counts():
    return {0: [0, 0], 1: [0, 0]}


def load_counts(path, start, days, minimum_followup_days):
    """Stream the CSV into cohort aggregates, requiring explicit linkability."""
    window_end = start + dt.timedelta(days=days)
    cutoff = window_end - dt.timedelta(days=minimum_followup_days)
    overall = _new_counts()
    by_size = defaultdict(_new_counts)
    by_author = defaultdict(_new_counts)
    by_repository = defaultdict(_new_counts)
    by_week = defaultdict(lambda: {
        "rows": 0,
        "linkable": 0,
        "human_authored_linkable": 0,
        "ai_human_authored_linkable": 0,
    })
    counters = defaultdict(int)

    with Path(path).open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"linkable", "author_type", "reverted_at", "merged_at"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "input lacks required corrected columns: " + ", ".join(sorted(missing))
            )
        for row in reader:
            counters["rows_total"] += 1
            try:
                merged = dt.datetime.fromisoformat(row["merged_at"].replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError):
                counters["invalid_merged_at"] += 1
                continue
            if not start <= merged < window_end:
                counters["outside_window"] += 1
                continue
            counters["rows_in_window"] += 1
            week = int((merged - start).total_seconds() // (7 * 86400)) + 1
            by_week[week]["rows"] += 1
            if row.get("linkable") != "1":
                counters["unlinked"] += 1
                continue
            counters["linkable"] += 1
            by_week[week]["linkable"] += 1
            author_type = (row.get("author_type") or "").casefold()
            if author_type == "bot":
                counters["bot_authored_excluded"] += 1
                continue
            if author_type != "user":
                counters["unknown_author_type_excluded"] += 1
                continue
            counters["human_authored_linkable"] += 1
            ai = 1 if row.get("is_ai") == "1" else 0
            by_week[week]["human_authored_linkable"] += 1
            by_week[week]["ai_human_authored_linkable"] += ai
            if merged >= cutoff:
                counters["right_censored"] += 1
                continue
            counters["cohort"] += 1
            if ai:
                for tool in filter(None, (row.get("ai_tools") or "").split(",")):
                    counters[f"ai_tool_{tool}"] += 1
            reverted_at_text = row.get("reverted_at") or ""
            reverted = 0
            if reverted_at_text:
                try:
                    reverted_at = dt.datetime.fromisoformat(
                        reverted_at_text.replace("Z", "+00:00")
                    )
                except ValueError as error:
                    raise ValueError(
                        f"invalid reverted_at for {row.get('repo')}#{row.get('pr')}: "
                        f"{reverted_at_text}"
                    ) from error
                if reverted_at < merged:
                    counters["revert_evidence_before_merge"] += 1
                elif reverted_at <= merged + dt.timedelta(days=minimum_followup_days):
                    reverted = 1
                    counters["reverts_within_followup"] += 1
                else:
                    counters["reverts_after_followup"] += 1
            try:
                size = float(row["pr_size"]) if row.get("pr_size") not in ("", "None", None) else None
            except ValueError:
                size = None
            repo = (row.get("repo") or "").casefold()
            author = row.get("author_assoc") or "NONE"
            for store, key in (
                (overall, None),
                (by_size, size_band(size)),
                (by_author, author),
                (by_repository, repo),
            ):
                counts = store if key is None else store[key]
                counts[ai][0] += 1
                counts[ai][1] += reverted
    return {
        "window_end": window_end,
        "cutoff": cutoff,
        "overall": overall,
        "by_size": by_size,
        "by_author": by_author,
        "by_repository": by_repository,
        "by_week": {week: dict(values) for week, values in sorted(by_week.items())},
        "counters": dict(counters),
    }


def analyze(path, start, days, minimum_followup_days, permutation_draws, seed):
    data = load_counts(path, start, days, minimum_followup_days)
    overall = data["overall"]
    ai = overall[1]
    non = overall[0]
    total = ai[0] + non[0]
    total_reverts = ai[1] + non[1]
    if not ai[0] or not non[0]:
        raise RuntimeError("cohort must contain both AI and non-AI pull requests")

    rate_ai, rate_non, difference, low, high, z_value, p_value = two_proportion(
        ai[1], ai[0], non[1], non[0]
    )
    size_rows = []
    standardized_difference = 0.0
    standardization_weight = 0.0
    for name in SIZE_BANDS:
        counts = data["by_size"].get(name, _new_counts())
        group_ai, group_non = counts[1], counts[0]
        if not group_ai[0] or not group_non[0]:
            continue
        values = two_proportion(group_ai[1], group_ai[0], group_non[1], group_non[0])
        weight = group_non[0] / non[0]
        standardized_difference += weight * values[2]
        standardization_weight += weight
        size_rows.append(
            {
                "band": name,
                "ai_count": group_ai[0],
                "ai_reverts": group_ai[1],
                "non_ai_count": group_non[0],
                "non_ai_reverts": group_non[1],
                "difference_pp": values[2],
                "ci_95_pp": [values[3], values[4]],
                "p": values[6],
                "non_ai_weight": weight,
            }
        )
    if standardization_weight:
        standardized_difference /= standardization_weight

    author_candidates = []
    for name, counts in data["by_author"].items():
        group_ai, group_non = counts[1], counts[0]
        if group_ai[0] >= 30 and group_non[0] >= 30:
            values = two_proportion(group_ai[1], group_ai[0], group_non[1], group_non[0])
            author_candidates.append((name, group_ai, group_non, values))
    adjusted = bh([entry[3][6] for entry in author_candidates]) if author_candidates else []
    author_rows = []
    for (name, group_ai, group_non, values), adjusted_p in zip(author_candidates, adjusted):
        author_rows.append(
            {
                "association": name,
                "ai_count": group_ai[0],
                "ai_reverts": group_ai[1],
                "non_ai_count": group_non[0],
                "non_ai_reverts": group_non[1],
                "difference_pp": values[2],
                "p": values[6],
                "p_adjusted": float(adjusted_p),
            }
        )
    author_rows.sort(key=lambda row: -(row["ai_count"] + row["non_ai_count"]))

    higher = lower = ties = eligible_repositories = 0
    for counts in data["by_repository"].values():
        group_ai, group_non = counts[1], counts[0]
        if group_ai[0] < 20 or group_non[0] < 20:
            continue
        eligible_repositories += 1
        rate_difference = group_ai[1] / group_ai[0] - group_non[1] / group_non[0]
        if rate_difference > 0:
            higher += 1
        elif rate_difference < 0:
            lower += 1
        else:
            ties += 1

    permutation = permutation_result(
        ai[1], ai[0], total_reverts, total, permutation_draws, seed
    )
    clustered = cluster_robust_difference(data["by_repository"])
    weekly_coverage = []
    for week, values in data["by_week"].items():
        weekly_coverage.append({
            "week": week,
            **values,
            "linkable_pct": float(
                100 * values["linkable"] / values["rows"]
            ) if values["rows"] else math.nan,
            "ai_pct_of_human_linkable": float(
                100 * values["ai_human_authored_linkable"]
                / values["human_authored_linkable"]
            ) if values["human_authored_linkable"] else None,
        })

    input_path = Path(path)
    try:
        input_label = str(input_path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        input_label = input_path.name

    return {
        "input": input_label,
        "window_start_utc": start.isoformat(),
        "window_end_exclusive_utc": data["window_end"].isoformat(),
        "cohort_cutoff_exclusive_utc": data["cutoff"].isoformat(),
        "followup_days": minimum_followup_days,
        "counters": data["counters"],
        "weekly_coverage": weekly_coverage,
        "overall": {
            "ai_count": ai[0],
            "ai_reverts": ai[1],
            "ai_rate_pct": rate_ai,
            "non_ai_count": non[0],
            "non_ai_reverts": non[1],
            "non_ai_rate_pct": rate_non,
            "difference_pp": difference,
            "ci_95_pp": [low, high],
            "z": z_value,
            "p": p_value,
        },
        "size_strata": size_rows,
        "size_standardized_difference_pp": standardized_difference,
        "author_strata": author_rows,
        "within_repository_sign_test": {
            "minimum_per_group": 20,
            "eligible_repositories": eligible_repositories,
            "higher": higher,
            "lower": lower,
            "ties_excluded": ties,
            "two_sided_exact_p": exact_binomial_sign_p(higher, lower),
        },
        "permutation": permutation,
        "repository_cluster_robust": clustered,
    }


def render(summary):
    overall = summary["overall"]
    lines = [
        "=" * 78,
        "REVERT ANALYSIS - LINKABLE PULL REQUESTS WITH A FIXED 14-DAY OUTCOME WINDOW",
        "=" * 78,
        f"window: {summary['window_start_utc']} to {summary['window_end_exclusive_utc']} (end exclusive)",
        f"cohort cutoff: {summary['cohort_cutoff_exclusive_utc']} (end exclusive)",
        f"rows total: {summary['counters'].get('rows_total', 0):,}",
        f"linkable rows: {summary['counters'].get('linkable', 0):,}",
        f"cohort rows: {summary['counters'].get('cohort', 0):,}",
        "",
        "1) OVERALL TWO-PROPORTION COMPARISON",
        f"   AI {overall['ai_rate_pct']:.3f}% ({overall['ai_reverts']}/{overall['ai_count']}) vs "
        f"non-AI {overall['non_ai_rate_pct']:.3f}% "
        f"({overall['non_ai_reverts']}/{overall['non_ai_count']})",
        f"   difference {overall['difference_pp']:+.3f} pp | 95% CI "
        f"[{overall['ci_95_pp'][0]:+.3f}, {overall['ci_95_pp'][1]:+.3f}] | "
        f"z={overall['z']:.2f} | p={overall['p']:.6f}",
        "",
        "2) SIZE STRATA",
    ]
    for row in summary["size_strata"]:
        lines.append(
            f"   {row['band']:11} difference {row['difference_pp']:+.3f} pp | "
            f"AI {row['ai_reverts']}/{row['ai_count']} | "
            f"non-AI {row['non_ai_reverts']}/{row['non_ai_count']} | p={row['p']:.6f}"
        )
    lines.extend(
        [
            f"   standardized to non-AI size mix: "
            f"{summary['size_standardized_difference_pp']:+.3f} pp",
            "",
            "3) AUTHOR ASSOCIATION STRATA (BH corrected across reported groups)",
        ]
    )
    for row in summary["author_strata"]:
        lines.append(
            f"   {row['association']:14} difference {row['difference_pp']:+.3f} pp | "
            f"p={row['p']:.6f} | p_adj={row['p_adjusted']:.6f}"
        )
    sign = summary["within_repository_sign_test"]
    lines.extend(
        [
            "",
            "4) WITHIN-REPOSITORY SIGN TEST",
            f"   eligible {sign['eligible_repositories']} | higher {sign['higher']} | "
            f"lower {sign['lower']} | ties excluded {sign['ties_excluded']} | "
            f"exact p={sign['two_sided_exact_p']:.6f}",
            "",
            "5) HYPERGEOMETRIC RANDOM-LABEL SENSITIVITY",
            f"   observed {summary['permutation']['observed_ai_reverts']} | "
            f"expected {summary['permutation']['expected_ai_reverts']:.1f} | "
            f"sd {summary['permutation']['standard_deviation']:.2f} | "
            f"p={summary['permutation']['two_sided_p']:.6f} | "
            f"draws {summary['permutation']['draws']:,}",
            "",
            "6) REPOSITORY-CLUSTERED SANDWICH INFERENCE",
            f"   difference {summary['repository_cluster_robust']['difference_pp']:+.3f} pp | "
            f"clustered SE {summary['repository_cluster_robust']['standard_error_pp']:.3f} pp | "
            f"95% CI [{summary['repository_cluster_robust']['ci_95_pp'][0]:+.3f}, "
            f"{summary['repository_cluster_robust']['ci_95_pp'][1]:+.3f}] | "
            f"p={summary['repository_cluster_robust']['p']:.6f} | "
            f"repositories {summary['repository_cluster_robust']['repositories']:,}",
        ]
    )
    lines.extend(
        [
            "",
            "7) LINKABILITY AND STRICT TAG SHARE BY MERGE WEEK",
        ]
    )
    for row in summary["weekly_coverage"]:
        ai_share = (
            f"{row['ai_pct_of_human_linkable']:.3f}%"
            if row["ai_pct_of_human_linkable"] is not None else "n/a"
        )
        lines.append(
            f"   week {row['week']} rows {row['rows']:,} | linkable "
            f"{row['linkable_pct']:.1f}% | User linkable "
            f"{row['human_authored_linkable']:,} | strict AI {ai_share}"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--manifest")
    parser.add_argument("--start", default="2025-08-01")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--minimum-followup-days", type=int, default=14)
    parser.add_argument("--permutation-draws", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    args = parser.parse_args()
    if not 0 < args.minimum_followup_days < args.days:
        parser.error("minimum follow-up must be positive and shorter than the collection window")
    start = dt.datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    input_path = Path(args.input)
    manifest_path = Path(args.manifest) if args.manifest else input_path.with_suffix(
        input_path.suffix + ".manifest.json"
    )
    manifest = validate_manifest(input_path, manifest_path, start, args.days)
    summary = analyze(
        input_path,
        start,
        args.days,
        args.minimum_followup_days,
        args.permutation_draws,
        args.seed,
    )
    summary["collector_manifest"] = {
        "path": manifest_path.name,
        "requested_hours": manifest["requested_hours"],
        "completed_hours": manifest["completed_hours"],
        "csv_bytes": manifest["csv_bytes"],
        "csv_sha256": manifest["csv_sha256"],
    }
    report = render(summary)
    print(report, end="")
    Path(args.report).write_text(report)
    Path(args.summary).write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
