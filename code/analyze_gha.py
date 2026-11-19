#!/usr/bin/env python3
"""Checkpoint-3 analysis on the REAL GH Archive dataset (data/gha_prs.csv).

Restricts to linkable PRs (head/merge SHA seen in a push, so the AI flag is valid). Reuses the
validated stats from analyze.py: Mann-Whitney (normal approx), Cliff's delta + bootstrap CI,
Benjamini-Hochberg correction. Adds the confounder controls that decide credibility: size +
author-association imbalance, WITHIN-REPO comparison, SIZE-STRATIFIED time-to-merge, and a
MONTH-matched check (AI PRs concentrate in later months, so month is a real confounder).

Writes a human-readable report to data/analysis_real.txt and a JSON summary. Honest: reports what
is true, including null results.
"""
import json
import hashlib
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from analyze import mann_whitney, cliffs_delta, boot_ci_delta, bh, interp_delta
from robustness import two_proportion, cluster_robust_difference
from gha_run_year import BLOCK_HOURS, BLOCK_START_HOUR, CONTEXT_HOURS, MONTHS

ROOT = Path(__file__).resolve().parent.parent
PRS = ROOT / "data" / "gha_prs.csv"
MANIFEST = PRS.with_suffix(PRS.suffix + ".manifest.json")
OUT = ROOT / "data" / "analysis_real.txt"
BOOT_N = 1000
PAIRED_REPO_BOOT_N = 10_000
SEED = 0
METRICS = ["time_to_merge_h", "review_latency_h", "review_rounds",
           "commits", "pr_size", "files_changed", "comments", "review_comments"]
CONTAINED_METRICS = {"review_latency_h", "review_rounds"}
PRIMARY = ["time_to_merge_h", "review_latency_h", "review_rounds", "comments"]


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(data_path=PRS, manifest_path=MANIFEST):
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get("status") != "complete":
        raise ValueError("review manifest is not complete")
    if manifest.get("requested_hours") != manifest.get("completed_hours"):
        raise ValueError("review manifest reports incomplete hours")
    if manifest.get("block_start_hour_utc") != BLOCK_START_HOUR:
        raise ValueError("review manifest block start does not match analysis")
    if manifest.get("block_hours") != BLOCK_HOURS:
        raise ValueError("review manifest block duration does not match analysis")
    if manifest.get("context_hours_per_block") != CONTEXT_HOURS:
        raise ValueError("review manifest lacks the required merge-SHA context hour")
    expected_hours = len(MONTHS) * (BLOCK_HOURS + CONTEXT_HOURS)
    if manifest.get("requested_hours") != expected_hours:
        raise ValueError("review manifest has the wrong total source-hour count")
    if len(manifest.get("months", [])) != len(MONTHS):
        raise ValueError("review manifest has the wrong monthly block count")
    data_path = Path(data_path)
    if manifest.get("csv_bytes") != data_path.stat().st_size:
        raise ValueError("review CSV byte size does not match manifest")
    if manifest.get("csv_sha256") != file_sha256(data_path):
        raise ValueError("review CSV SHA-256 does not match manifest")
    return manifest


def contained_review_rows(df):
    """Return PRs whose complete pre-merge review history lies in one sampled block."""
    block_start = pd.to_datetime(
        df["month"].astype(str) + f"-15T{BLOCK_START_HOUR:02d}:00:00Z",
        utc=True,
        errors="coerce",
    )
    created = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    merged = pd.to_datetime(df["merged_at"], utc=True, errors="coerce")
    block_end = block_start + pd.Timedelta(hours=BLOCK_HOURS)
    return df[(created >= block_start) & (merged >= block_start) & (merged < block_end)].copy()


def within_repo_diffs(df, metric, minimum_per_group=5):
    """Return AI minus non-AI repository medians for repositories with both groups."""
    grouped = (
        df[["repo", "is_ai", metric]]
        .dropna(subset=[metric])
        .groupby(["repo", "is_ai"], sort=False)[metric]
        .agg(["count", "median"])
        .unstack("is_ai")
    )
    required = [("count", 0), ("count", 1), ("median", 0), ("median", 1)]
    if any(column not in grouped.columns for column in required):
        return np.array([], dtype=float)
    eligible = grouped[
        (grouped[("count", 0)] >= minimum_per_group)
        & (grouped[("count", 1)] >= minimum_per_group)
    ]
    return (eligible[("median", 1)] - eligible[("median", 0)]).to_numpy()


def exact_sign_p(higher, lower):
    total = higher + lower
    if total == 0:
        return 1.0
    extreme = min(higher, lower)
    tail = sum(math.comb(total, value) for value in range(extreme + 1)) / (2**total)
    return min(1.0, 2 * tail)


def paired_repository_sensitivity(df, metric, draws, seed):
    """Summarize equal-weighted repositories that contain both groups."""
    differences = within_repo_diffs(df, metric, minimum_per_group=1)
    if not len(differences):
        return None
    rng = np.random.default_rng(seed)
    bootstrap = np.median(
        rng.choice(differences, size=(draws, len(differences)), replace=True),
        axis=1,
    )
    higher = int((differences > 0).sum())
    lower = int((differences < 0).sum())
    ties = int((differences == 0).sum())
    return {
        "repositories": int(len(differences)),
        "median_difference": float(np.median(differences)),
        "ci_95": [float(np.percentile(bootstrap, 2.5)), float(np.percentile(bootstrap, 97.5))],
        "higher": higher,
        "lower": lower,
        "ties_excluded": ties,
        "exact_sign_p": exact_sign_p(higher, lower),
        "bootstrap_resamples": draws,
        "seed": seed,
    }



def _normal_two_sided_p(z_value):
    if not math.isfinite(z_value):
        return math.nan
    return math.erfc(abs(z_value) / math.sqrt(2))


def _review_counts_by_repository(frame):
    """Count rows and qualifying formal-review events by repository and tag."""
    working = frame[["repo", "is_ai", "review_latency_h"]].copy()
    working["reviewed"] = working["review_latency_h"].notna().astype(int)
    grouped = (
        working.groupby(["repo", "is_ai"], sort=False)["reviewed"]
        .agg(["count", "sum"])
        .reset_index()
    )
    result = {}
    for row in grouped.itertuples(index=False):
        counts = result.setdefault(str(row.repo), {0: [0, 0], 1: [0, 0]})
        counts[int(row.is_ai)] = [int(row.count), int(row.sum)]
    return result


def _review_counts_by_strata(frame, columns):
    """Count rows and formal-review events by arbitrary strata and tag."""
    working = frame[[*columns, "is_ai", "review_latency_h"]].copy()
    working["reviewed"] = working["review_latency_h"].notna().astype(int)
    grouped = (
        working.groupby([*columns, "is_ai"], sort=False, observed=True)["reviewed"]
        .agg(["count", "sum"])
        .reset_index()
    )
    result = {}
    for row in grouped.itertuples(index=False, name=None):
        key = tuple(str(value) for value in row[:len(columns)])
        group = int(row[len(columns)])
        count = int(row[len(columns) + 1])
        events = int(row[len(columns) + 2])
        counts = result.setdefault(key, {0: [0, 0], 1: [0, 0]})
        counts[group] = [count, events]
    return result


def mantel_haenszel_risk_difference(by_repository):
    """Return a repository-stratified risk difference and CMH test."""
    numerator = weight = expected = variance_cmh = 0.0
    sum_p = sum_q = 0.0
    repositories = tagged_count = tagged_reviewed = 0
    for counts in by_repository.values():
        n1, y1 = counts[1]
        n0, y0 = counts[0]
        total = n1 + n0
        if not n1 or not n0 or total < 2:
            continue
        repositories += 1
        tagged_count += n1
        tagged_reviewed += y1
        weight += n1 * n0 / total
        numerator += (y1 * n0 - y0 * n1) / total
        events = y1 + y0
        non_events = total - events
        expected += n1 * events / total
        if total > 1:
            variance_cmh += n1 * n0 * events * non_events / (
                total**2 * (total - 1)
            )
        sum_p += (
            n1**2 * y0
            - n0**2 * y1
            + n1 * n0 * (n0 - n1) / 2
        ) / total**2
        sum_q += (
            y1 * (n0 - y0) + y0 * (n1 - y1)
        ) / (2 * total)
    if not weight:
        return None
    difference = numerator / weight
    sato_variance = max((difference * sum_p + sum_q) / weight**2, 0.0)
    sato_se = math.sqrt(sato_variance)
    cmh_numerator = sum(
        counts[1][1]
        for counts in by_repository.values()
        if counts[1][0] and counts[0][0]
    ) - expected
    cmh_z = cmh_numerator / math.sqrt(variance_cmh) if variance_cmh else math.nan
    return {
        "method": "repository-stratified Mantel-Haenszel risk difference",
        "repositories": repositories,
        "tagged_count": tagged_count,
        "tagged_reviewed": tagged_reviewed,
        "tagged_retained_pct": float(100 * tagged_count / sum(
            counts[1][0] for counts in by_repository.values()
        )),
        "difference_pp": float(100 * difference),
        "sato_standard_error_pp": float(100 * sato_se),
        "sato_ci_95_pp": [
            float(100 * (difference - 1.96 * sato_se)),
            float(100 * (difference + 1.96 * sato_se)),
        ],
        "cmh_z": float(cmh_z),
        "cmh_p": float(_normal_two_sided_p(cmh_z)),
    }


def _association_group(value):
    value = str(value).upper()
    if value == "OWNER":
        return "OWNER"
    if value in {"MEMBER", "COLLABORATOR"}:
        return "MEMBER_OR_COLLABORATOR"
    if value == "CONTRIBUTOR":
        return "CONTRIBUTOR"
    return "OUTSIDER_OR_UNKNOWN"


def standardized_review_incidence(frame, columns):
    """ATT-standardize review incidence and cluster the interval by repository."""
    working = frame[["repo", "is_ai", "review_latency_h", *columns]].copy()
    working["reviewed"] = working["review_latency_h"].notna().astype(int)
    for column in columns:
        working[column] = working[column].astype("string").fillna("<missing>")
    working["stratum"] = list(
        working[columns].itertuples(index=False, name=None)
    ) if columns else [("all",)] * len(working)
    grouped = (
        working.groupby(["repo", "stratum", "is_ai"], sort=False)["reviewed"]
        .agg(["count", "sum"])
        .reset_index()
    )
    cells = {}
    for row in grouped.itertuples(index=False):
        counts = cells.setdefault(row.stratum, {0: [0, 0], 1: [0, 0]})
        counts[int(row.is_ai)][0] += int(row.count)
        counts[int(row.is_ai)][1] += int(row.sum)
    total_tagged = sum(counts[1][0] for counts in cells.values())
    eligible = {
        key: counts for key, counts in cells.items()
        if counts[1][0] and counts[0][0]
    }
    retained_tagged = sum(counts[1][0] for counts in eligible.values())
    if not retained_tagged:
        return None
    weights = {
        key: counts[1][0] / retained_tagged for key, counts in eligible.items()
    }
    rates = {
        key: {
            1: counts[1][1] / counts[1][0],
            0: counts[0][1] / counts[0][0],
        }
        for key, counts in eligible.items()
    }
    difference = sum(
        weights[key] * (rates[key][1] - rates[key][0]) for key in eligible
    )
    scores = {}
    retained_repositories = set()
    for row in grouped.itertuples(index=False):
        key = row.stratum
        if key not in eligible:
            continue
        repo = str(row.repo)
        retained_repositories.add(repo)
        group = int(row.is_ai)
        denominator = eligible[key][group][0]
        residual = int(row.sum) - rates[key][group] * int(row.count)
        if group == 1:
            residual += (
                rates[key][1] - rates[key][0] - difference
            ) * int(row.count)
        direction = 1 if group == 1 else -1
        scores[repo] = scores.get(repo, 0.0) + (
            direction * weights[key] * residual / denominator
        )
    repository_count = len(retained_repositories)
    variance = sum(value * value for value in scores.values())
    if repository_count > 1:
        variance *= repository_count / (repository_count - 1)
    standard_error = math.sqrt(max(variance, 0.0))
    z_value = difference / standard_error if standard_error else math.nan
    strata = []
    for key, counts in sorted(eligible.items(), key=lambda item: str(item[0])):
        strata.append({
            "values": [str(value) for value in key],
            "tagged_count": counts[1][0],
            "tagged_reviewed": counts[1][1],
            "comparison_count": counts[0][0],
            "comparison_reviewed": counts[0][1],
            "weight_pct": float(100 * weights[key]),
        })
    return {
        "columns": columns,
        "difference_pp": float(100 * difference),
        "repository_clustered_standard_error_pp": float(100 * standard_error),
        "repository_clustered_ci_95_pp": [
            float(100 * (difference - 1.96 * standard_error)),
            float(100 * (difference + 1.96 * standard_error)),
        ],
        "z": float(z_value),
        "p": float(_normal_two_sided_p(z_value)),
        "repositories": repository_count,
        "tagged_retained": retained_tagged,
        "tagged_retained_pct": float(100 * retained_tagged / total_tagged),
        "dropped_tagged": total_tagged - retained_tagged,
        "strata": strata,
    }


def _top_share(counts, number):
    total = counts.sum()
    return float(100 * counts.nlargest(number).sum() / total) if total else 0.0


def review_incidence_analysis(contained):
    """Analyze whether a qualifying formal-review event is recorded before merge."""
    frame = contained.copy()
    frame["reviewed"] = frame["review_latency_h"].notna().astype(int)
    tagged = frame[frame.is_ai == 1]
    comparison = frame[frame.is_ai == 0]
    tagged_reviewed = int(tagged.reviewed.sum())
    comparison_reviewed = int(comparison.reviewed.sum())
    unclustered = two_proportion(
        tagged_reviewed, len(tagged), comparison_reviewed, len(comparison)
    )
    by_repository = _review_counts_by_repository(frame)
    clustered = cluster_robust_difference(by_repository)
    mh = mantel_haenszel_risk_difference(by_repository)

    tagged_repo_counts = (
        tagged.groupby("repo").size().sort_index().sort_values(
            ascending=False, kind="stable"
        )
    )
    reviewed_repo_counts = (
        tagged[tagged.reviewed == 1].groupby("repo").size().sort_index().sort_values(
            ascending=False, kind="stable"
        )
    )
    effective_clusters = (
        float(tagged_repo_counts.sum() ** 2 / (tagged_repo_counts.pow(2).sum()))
        if len(tagged_repo_counts) else 0.0
    )
    concentration = {
        "tagged_repositories": int(len(tagged_repo_counts)),
        "tagged_reviewed_repositories": int(len(reviewed_repo_counts)),
        "effective_tagged_clusters": effective_clusters,
        "tagged_pr_share_pct": {
            f"top_{number}": _top_share(tagged_repo_counts, number)
            for number in (1, 5, 10)
        },
        "tagged_reviewed_share_pct": {
            f"top_{number}": _top_share(reviewed_repo_counts, number)
            for number in (1, 5, 10)
        },
    }

    frame["size_band"] = pd.cut(
        frame.pr_size, [-1, 50, 300, math.inf],
        labels=["small<=50", "medium51-300", "large>300"],
    ).astype("string").fillna("<missing>")
    frame["association_group"] = frame.author_assoc.map(_association_group)
    standardized = {
        "size": standardized_review_incidence(frame, ["size_band"]),
        "month": standardized_review_incidence(frame, ["month"]),
        "author_association": standardized_review_incidence(
            frame, ["association_group"]
        ),
        "joint": standardized_review_incidence(
            frame, ["size_band", "month", "association_group"]
        ),
    }
    repository_covariate_stratified = mantel_haenszel_risk_difference(
        _review_counts_by_strata(
            frame, ["repo", "size_band", "month", "association_group"]
        )
    )

    top_repositories = list(tagged_repo_counts.head(5).index)
    leave_out = []
    scenarios = [(f"top_{index}", [repo]) for index, repo in enumerate(
        top_repositories, start=1
    )]
    if top_repositories:
        scenarios.append(("top_5_together", top_repositories))
    for label, repositories in scenarios:
        reduced = frame[~frame.repo.isin(repositories)]
        reduced_counts = _review_counts_by_repository(reduced)
        reduced_clustered = cluster_robust_difference(reduced_counts)
        reduced_mh = mantel_haenszel_risk_difference(reduced_counts)
        reduced_tagged = reduced[reduced.is_ai == 1]
        reduced_comparison = reduced[reduced.is_ai == 0]
        leave_out.append({
            "scenario": label,
            "tagged_count": int(len(reduced_tagged)),
            "difference_pp": float(100 * (
                reduced_tagged.reviewed.mean() - reduced_comparison.reviewed.mean()
            )),
            "repository_clustered_ci_95_pp": reduced_clustered["ci_95_pp"],
            "mantel_haenszel_difference_pp": (
                reduced_mh["difference_pp"] if reduced_mh else math.nan
            ),
        })

    return {
        "outcome": (
            "at least one qualifying pre-merge PullRequestReviewEvent from a "
            "non-author account whose GitHub type is not Bot"
        ),
        "scope": "User-authored, linkable PRs created and merged in one sampled block",
        "tagged_count": int(len(tagged)),
        "tagged_reviewed": tagged_reviewed,
        "tagged_rate_pct": unclustered[0],
        "comparison_count": int(len(comparison)),
        "comparison_reviewed": comparison_reviewed,
        "comparison_rate_pct": unclustered[1],
        "difference_pp": unclustered[2],
        "newcombe_unclustered_ci_95_pp": [unclustered[3], unclustered[4]],
        "pooled_z_unclustered": unclustered[5],
        "pooled_p_unclustered": unclustered[6],
        "repository_clustered": clustered,
        "repository_stratified": mh,
        "repository_covariate_stratified": repository_covariate_stratified,
        "standardized": standardized,
        "concentration": concentration,
        "leave_top_repositories_out": leave_out,
    }


def tool_sensitivity(df, contained, minimum=30):
    results = {}
    tool_names = sorted({
        tool
        for value in df.loc[df.is_ai == 1, "ai_tools"].fillna("")
        for tool in value.split(",")
        if tool
    })
    non_ai = df[df.is_ai == 0]
    non_ai_contained = contained[contained.is_ai == 0]
    for tool in tool_names:
        pattern = rf"(?:^|,){re.escape(tool)}(?:,|$)"
        tagged = df[df["ai_tools"].fillna("").str.contains(pattern, regex=True)]
        tagged_contained = contained[
            contained["ai_tools"].fillna("").str.contains(pattern, regex=True)
        ]
        if len(tagged) < minimum:
            continue
        metrics = {}
        for metric, ai_group, comparison in (
            ("time_to_merge_h", tagged, non_ai),
            ("pr_size", tagged, non_ai),
            ("review_rounds", tagged_contained, non_ai_contained),
        ):
            ai_values = ai_group[metric].dropna().values
            non_values = comparison[metric].dropna().values
            metrics[metric] = {
                "n_ai": int(len(ai_values)),
                "median_ai": float(np.median(ai_values)),
                "median_non_ai": float(np.median(non_values)),
                "cliffs_delta": float(cliffs_delta(ai_values, non_values)),
            }
        results[tool] = {"tagged_pull_requests": int(len(tagged)), "metrics": metrics}
    return results


def main():
    np.random.seed(SEED)
    out = []
    def p(*a):
        line = " ".join(str(x) for x in a)
        out.append(line)
        print(line, flush=True)

    manifest = validate_manifest()
    df = pd.read_csv(PRS, dtype={"ai_tools": "string"}, low_memory=False)
    for c in METRICS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["is_ai"] = pd.to_numeric(df["is_ai"], errors="coerce").fillna(0).astype(int)
    df["linkable"] = pd.to_numeric(df["linkable"], errors="coerce").fillna(0).astype(int)

    total_rows = len(df)
    monthly_coverage = {}
    for month, group in df.groupby("month"):
        linkable_count = int((group.linkable == 1).sum())
        linked_group = group[group.linkable == 1]
        linked_types = linked_group["author_type"].fillna("").str.casefold()
        bot_count = int((linked_types == "bot").sum())
        unknown_count = int((~linked_types.isin(("user", "bot"))).sum())
        monthly_coverage[str(month)] = {
            "rows": int(len(group)),
            "linkable": linkable_count,
            "linkable_pct": float(100 * linkable_count / len(group)),
            "bot_authored": bot_count,
            "unknown_author_type": unknown_count,
        }
    linkable_all = df[df.linkable == 1].copy()
    author_types = linkable_all["author_type"].fillna("").str.casefold()
    bot_authored = linkable_all[author_types == "bot"]
    unknown_authored = linkable_all[~author_types.isin(("user", "bot"))]
    df = linkable_all[author_types == "user"].copy()
    contained = contained_review_rows(df)
    ai = df[df.is_ai == 1]; non = df[df.is_ai == 0]
    ai_contained = contained[contained.is_ai == 1]
    non_contained = contained[contained.is_ai == 0]
    tool_counts = {}
    for value in ai["ai_tools"].fillna(""):
        for tool in filter(None, value.split(",")):
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
    p("=" * 78)
    p("CHECKPOINT 3 - REAL DATA (GH Archive, Oct 2024 - Sep 2025)")
    p("=" * 78)
    p(f"rows total {total_rows:,} | linkable {len(linkable_all):,} | "
      f"bot-authored excluded {len(bot_authored):,} | "
      f"unknown author type excluded {len(unknown_authored):,} | "
      f"human-authored analyzed {len(df):,}")
    p(f"AI {len(ai):,} | non-AI {len(non):,} | distinct repos {df.repo.nunique():,}")
    tool_results = tool_sensitivity(df, contained)
    p(f"AI tools: {dict(sorted(tool_counts.items(), key=lambda item: (-item[1], item[0])))}")
    p(f"tool sensitivity: {tool_results}")
    p(f"monthly coverage: {monthly_coverage}")
    p(f"contained review subset {len(contained):,} | AI {len(ai_contained):,} | "
      f"non-AI {len(non_contained):,}")
    p("")

    p("--- CONFOUNDER CHECK ---")
    p(f"median PR size:  AI {ai.pr_size.median():.0f}  vs  non-AI {non.pr_size.median():.0f}")
    p(f"median commits:  AI {ai.commits.median():.0f}  vs  non-AI {non.commits.median():.0f}")
    for g, sub in [("AI", ai), ("non-AI", non)]:
        mix = (sub.author_assoc.value_counts(normalize=True) * 100).round(0).to_dict()
        p(f"  author_assoc {g}: " + ", ".join(f"{k} {v:.0f}%" for k, v in list(mix.items())[:4]))
    # month imbalance (temporal confounder)
    aimo = (ai.month.value_counts(normalize=True) * 100).round(1)
    p(f"  AI PRs concentrate in later months (top: {aimo.head(3).to_dict()})")
    p("")

    p("--- OVERALL (uncontrolled), per metric, BH-corrected ---")
    rows = []
    for m in METRICS:
        source = contained if m in CONTAINED_METRICS else df
        a = source[source.is_ai == 1][m].dropna().values
        b = source[source.is_ai == 0][m].dropna().values
        _, pv = mann_whitney(a, b)
        d = cliffs_delta(a, b)
        rows.append((m, pv, d, a, b))
    adj = bh([r[1] for r in rows])
    metric_results = []
    for (m, pv, d, a, b), ap in zip(rows, adj):
        lo, hi = boot_ci_delta(a, b, BOOT_N)
        sig = "SIG" if ap < 0.05 else "ns"
        metric_results.append({
            "metric": m,
            "n_ai": int(len(a)),
            "n_non_ai": int(len(b)),
            "median_ai": float(np.median(a)),
            "median_non_ai": float(np.median(b)),
            "cliffs_delta": float(d),
            "ci_95": [float(lo), float(hi)],
            "p": float(pv),
            "p_adjusted": float(ap),
            "effect": interp_delta(d),
        })
        p(f"  {m:17} n={len(a):,}/{len(b):,} | AI med {np.median(a):9.3f} | "
          f"non med {np.median(b):9.3f} | delta {d:+.3f} [{lo:+.3f},{hi:+.3f}] "
          f"{interp_delta(d):10} p_adj {ap:.6g} {sig}")
    p("")

    reviewed_ai = ai_contained.review_latency_h.notna().sum()
    reviewed_non = non_contained.review_latency_h.notna().sum()
    p("--- FORMAL REVIEW COVERAGE (contained subset) ---")
    p(f"  AI {reviewed_ai:,}/{len(ai_contained):,} ({100*reviewed_ai/len(ai_contained):.1f}%) | "
      f"non-AI {reviewed_non:,}/{len(non_contained):,} "
      f"({100*reviewed_non/len(non_contained):.1f}%)")
    review_incidence = review_incidence_analysis(contained)
    clustered_review = review_incidence["repository_clustered"]
    stratified_review = review_incidence["repository_stratified"]
    p(f"  difference {review_incidence['difference_pp']:+.3f} pp | Newcombe unclustered 95% CI "
      f"[{review_incidence['newcombe_unclustered_ci_95_pp'][0]:+.3f}, "
      f"{review_incidence['newcombe_unclustered_ci_95_pp'][1]:+.3f}] | "
      f"pooled p={review_incidence['pooled_p_unclustered']:.6g}")
    p(f"  repository-clustered difference {clustered_review['difference_pp']:+.3f} pp | "
      f"95% CI [{clustered_review['ci_95_pp'][0]:+.3f}, "
      f"{clustered_review['ci_95_pp'][1]:+.3f}] | "
      f"p={clustered_review['p']:.6g} | repositories {clustered_review['repositories']:,}")
    if stratified_review:
        p(f"  repository-stratified difference {stratified_review['difference_pp']:+.3f} pp | "
          f"Sato 95% CI [{stratified_review['sato_ci_95_pp'][0]:+.3f}, "
          f"{stratified_review['sato_ci_95_pp'][1]:+.3f}] | "
          f"CMH p={stratified_review['cmh_p']:.6g} | "
          f"repositories {stratified_review['repositories']:,} | tagged retained "
          f"{stratified_review['tagged_count']:,} "
          f"({stratified_review['tagged_retained_pct']:.1f}%)")
    combined_review = review_incidence["repository_covariate_stratified"]
    if combined_review:
        p(f"  repository+covariate-stratified difference "
          f"{combined_review['difference_pp']:+.3f} pp | Sato 95% CI "
          f"[{combined_review['sato_ci_95_pp'][0]:+.3f}, "
          f"{combined_review['sato_ci_95_pp'][1]:+.3f}] | "
          f"CMH p={combined_review['cmh_p']:.6g} | strata "
          f"{combined_review['repositories']:,} | tagged retained "
          f"{combined_review['tagged_count']:,} "
          f"({combined_review['tagged_retained_pct']:.1f}%)")
    p(f"  tagged repositories {review_incidence['concentration']['tagged_repositories']:,} | "
      f"effective tagged clusters "
      f"{review_incidence['concentration']['effective_tagged_clusters']:.1f} | "
      f"top-5 tagged share "
      f"{review_incidence['concentration']['tagged_pr_share_pct']['top_5']:.1f}%")
    for name, result in review_incidence["standardized"].items():
        if result:
            p(f"  standardized by {name:18} {result['difference_pp']:+.3f} pp | "
              f"clustered 95% CI [{result['repository_clustered_ci_95_pp'][0]:+.3f}, "
              f"{result['repository_clustered_ci_95_pp'][1]:+.3f}] | "
              f"tagged retained {result['tagged_retained']:,} "
              f"({result['tagged_retained_pct']:.1f}%)")
    for result in review_incidence["leave_top_repositories_out"]:
        p(f"  leave out {result['scenario']:14} difference "
          f"{result['difference_pp']:+.3f} pp | clustered 95% CI "
          f"[{result['repository_clustered_ci_95_pp'][0]:+.3f}, "
          f"{result['repository_clustered_ci_95_pp'][1]:+.3f}] | "
          f"MH {result['mantel_haenszel_difference_pp']:+.3f} pp")
    p("")

    p("--- WITHIN-REPO (each repo its own control; repos with >=5 AI & >=5 non-AI) ---")
    within_counts = {}
    for m in PRIMARY:
        source = contained if m in CONTAINED_METRICS else df
        diffs = within_repo_diffs(source, m)
        within_counts[m] = len(diffs)
        if len(diffs):
            p(f"  {m:17} repos={len(diffs):3d} | median per-repo (AI-nonAI) = {np.median(diffs):+.3f} "
              f"| AI lower in {100*(diffs<0).mean():.0f}% | ties {100*(diffs==0).mean():.0f}%")
        else:
            p(f"  {m:17} too few repos with both groups >=5")
    p("")

    p("--- PAIRED-REPOSITORY SENSITIVITY (>=1 observation per group) ---")
    paired_repository = {}
    for index, m in enumerate(METRICS):
        source = contained if m in CONTAINED_METRICS else df
        result = paired_repository_sensitivity(
            source, m, PAIRED_REPO_BOOT_N, SEED + 100 + index
        )
        if result is None:
            continue
        paired_repository[m] = result
        p(f"  {m:17} repos={result['repositories']:4d} | median diff "
          f"{result['median_difference']:+.3f} | 95% CI "
          f"[{result['ci_95'][0]:+.3f},{result['ci_95'][1]:+.3f}] | "
          f"higher/lower/ties {result['higher']}/{result['lower']}/{result['ties_excluded']} | "
          f"sign p={result['exact_sign_p']:.6g}")
    p("")

    p("--- SIZE-STRATIFIED time_to_merge_h ---")
    df["band"] = pd.cut(df.pr_size, [-1, 50, 300, 10**9], labels=["small<=50", "med51-300", "large>300"])
    for band, g in df.groupby("band", observed=True):
        a = g[g.is_ai == 1].time_to_merge_h.dropna().values
        b = g[g.is_ai == 0].time_to_merge_h.dropna().values
        if len(a) >= 10 and len(b) >= 10:
            _, pv = mann_whitney(a, b); d = cliffs_delta(a, b)
            p(f"  {band:11} AI med {np.median(a):8.2f} (n={len(a)}) | non med {np.median(b):8.2f} "
              f"(n={len(b)}) | delta {d:+.3f} {interp_delta(d):10} p {pv:.4g}")
    p("")

    p("--- MONTH-MATCHED time_to_merge_h (controls temporal confounder) ---")
    mdiffs = []
    monthly_ai = {}
    for mo, g in df.groupby("month"):
        a = g[g.is_ai == 1].time_to_merge_h.dropna(); b = g[g.is_ai == 0].time_to_merge_h.dropna()
        monthly_ai[str(mo)] = int(len(a))
        if len(a) >= 10 and len(b) >= 10:
            mdiffs.append(a.median() - b.median())
    if mdiffs:
        mdiffs = np.asarray(mdiffs)
        p(f"  months={len(mdiffs)} | median per-month (AI-nonAI) = {np.median(mdiffs):+.3f}h "
          f"| AI higher {int((mdiffs>0).sum())} | lower {int((mdiffs<0).sum())} | "
          f"ties {int((mdiffs==0).sum())}")
        p(f"  AI counts by month: {monthly_ai}")

    summary = {
        "rows_total": int(total_rows),
        "linkable": int(len(linkable_all)),
        "bot_authored_excluded": int(len(bot_authored)),
        "unknown_author_type_excluded": int(len(unknown_authored)),
        "human_authored_analyzed": int(len(df)),
        "ai": int(len(ai)),
        "non_ai": int(len(non)),
        "ai_tools": tool_counts,
        "tool_sensitivity": tool_results,
        "monthly_coverage": monthly_coverage,
        "repos": int(df.repo.nunique()),
        "contained": int(len(contained)),
        "contained_ai": int(len(ai_contained)),
        "contained_non_ai": int(len(non_contained)),
        "reviewed_ai": int(reviewed_ai),
        "reviewed_non_ai": int(reviewed_non),
        "review_incidence": review_incidence,
        "bootstrap_resamples": BOOT_N,
        "paired_repository_bootstrap_resamples": PAIRED_REPO_BOOT_N,
        "seed": SEED,
        "within_repo_counts": within_counts,
        "metric_results": metric_results,
        "paired_repository": paired_repository,
        "monthly_ai": monthly_ai,
        "collector_manifest": {
            "path": MANIFEST.name,
            "requested_hours": manifest["requested_hours"],
            "completed_hours": manifest["completed_hours"],
            "csv_bytes": manifest["csv_bytes"],
            "csv_sha256": manifest["csv_sha256"],
        },
    }
    OUT.write_text("\n".join(out))
    (ROOT / "data" / "analysis_real_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False)
    )
    p(f"\n[report -> {OUT}]")


if __name__ == "__main__":
    main()
