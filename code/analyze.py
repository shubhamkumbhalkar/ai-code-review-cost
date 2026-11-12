#!/usr/bin/env python3
"""Analyze AI vs non-AI PRs rigorously, using only pandas + numpy (no scipy).

For each metric:
  - medians per group + n
  - Mann-Whitney U (normal approximation with tie correction) -> two-sided p
  - Cliff's delta effect size with a bootstrap 95% CI
  - Benjamini-Hochberg correction across the metrics
Then the confounder checks that decide credibility:
  - size + author-association imbalance between groups (described, not hidden)
  - WITHIN-REPO paired comparison (each repo's AI median vs its own non-AI median), so results
    are not driven by which repos or who merges
  - SIZE-STRATIFIED comparison (small/medium/large PRs) to separate "AI" from "bigger PR"

Honest by design: reports whatever is true, including null results.
"""
import csv
import json
import math
import random
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PRS = ROOT / "data" / "prs.csv"
METRICS = ["time_to_merge_h", "review_latency_h", "review_rounds", "num_commits", "pr_size", "files_changed"]
PRIMARY = ["time_to_merge_h", "review_latency_h", "review_rounds"]  # review-cost headline metrics


def mann_whitney(x, y):
    """Two-sided Mann-Whitney U with normal approximation + tie correction. Returns (U, p)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return float("nan"), float("nan")
    allv = np.concatenate([x, y])
    order = allv.argsort()
    ranks = np.empty(len(allv), float)
    ranks[order] = np.arange(1, len(allv) + 1)
    # average ties
    _, inv, counts = np.unique(allv, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts)); np.add.at(sums, inv, ranks)
    avg = sums / counts
    ranks = avg[inv]
    R1 = ranks[:n1].sum()
    U1 = R1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    tie = (counts ** 3 - counts).sum()
    sigma = math.sqrt(n1 * n2 / 12 * ((n1 + n2 + 1) - tie / ((n1 + n2) * (n1 + n2 - 1))))
    if sigma == 0:
        return U1, 1.0
    z = (U1 - mu) / sigma
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return U1, p


def cliffs_delta(x, y):
    """Cliff's delta via U: delta = 2U/(n1*n2) - 1. Range -1..1."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) == 0 or len(y) == 0:
        return float("nan")
    U, _ = mann_whitney(x, y)
    return 2 * U / (len(x) * len(y)) - 1


def boot_ci_delta(x, y, n=1000):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or len(y) < 3:
        return (float("nan"), float("nan"))
    ds = []
    for _ in range(n):
        ds.append(cliffs_delta(np.random.choice(x, len(x), True), np.random.choice(y, len(y), True)))
    return (round(float(np.percentile(ds, 2.5)), 3), round(float(np.percentile(ds, 97.5)), 3))


def bh(pvals):
    """Benjamini-Hochberg adjusted p-values."""
    p = np.asarray(pvals, float)
    n = len(p)
    order = p.argsort()
    adj = np.empty(n)
    prev = 1.0
    for rank, idx in enumerate(reversed(order)):
        i = n - rank
        prev = min(prev, p[idx] * n / i)
        adj[idx] = prev
    return adj


def interp_delta(d):
    a = abs(d)
    return "negligible" if a < .147 else "small" if a < .33 else "medium" if a < .474 else "large"


def main():
    random.seed(0); np.random.seed(0)
    df = pd.read_csv(PRS)
    for c in METRICS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    ai = df[df.is_ai == 1]; non = df[df.is_ai == 0]
    print(f"=== DATASET ===\nPRs: {len(df)} | AI: {len(ai)} | non-AI: {len(non)} | repos: {df.repo.nunique()}\n")

    print("=== CONFOUNDER CHECK (why controlled analysis matters) ===")
    print(f"median PR size:  AI {ai.pr_size.median():.0f}  vs  non-AI {non.pr_size.median():.0f}")
    print("author association mix:")
    for g, sub in [("AI", ai), ("non-AI", non)]:
        mix = (sub.author_assoc.value_counts(normalize=True) * 100).round(0).to_dict()
        print(f"  {g}: " + ", ".join(f"{k} {v:.0f}%" for k, v in list(mix.items())[:4]))
    print()

    print("=== OVERALL (uncontrolled) per metric ===")
    p004 = []
    rows = []
    for m in METRICS:
        a = ai[m].dropna(); b = non[m].dropna()
        _, p = mann_whitney(a, b)
        d = cliffs_delta(a, b)
        rows.append((m, p, d))
        p004.append(p)
    adj = bh([r[1] for r in rows])
    for (m, p, d), ap in zip(rows, adj):
        a = ai[m].dropna(); b = non[m].dropna()
        lo, hi = boot_ci_delta(a.values, b.values, 500)
        sig = "SIG" if ap < 0.05 else "ns"
        print(f"  {m:18} AI med {a.median():8.2f} | non med {b.median():8.2f} | "
              f"delta {d:+.3f} [{lo:+.2f},{hi:+.2f}] {interp_delta(d):10} p_adj {ap:.4f} {sig}")

    print("\n=== WITHIN-REPO (each repo as its own control; needs >=5 AI & >=5 non-AI) ===")
    for m in PRIMARY:
        diffs = []
        for repo, g in df.groupby("repo"):
            a = g[g.is_ai == 1][m].dropna(); b = g[g.is_ai == 0][m].dropna()
            if len(a) >= 5 and len(b) >= 5:
                diffs.append(a.median() - b.median())
        if diffs:
            diffs = np.array(diffs)
            share = (diffs < 0).mean() * 100
            print(f"  {m:18} repos={len(diffs):2d} | median of (AI-nonAI) per-repo medians = {np.median(diffs):+.2f} "
                  f"| AI faster/less in {share:.0f}% of repos")
        else:
            print(f"  {m:18} too few repos with both groups >=5")

    print("\n=== SIZE-STRATIFIED time_to_merge_h (separates 'AI' from 'bigger PR') ===")
    df["size_band"] = pd.cut(df.pr_size, [-1, 50, 300, 10 ** 9], labels=["small(<=50)", "med(51-300)", "large(>300)"])
    for band, g in df.groupby("size_band", observed=True):
        a = g[g.is_ai == 1].time_to_merge_h.dropna(); b = g[g.is_ai == 0].time_to_merge_h.dropna()
        if len(a) >= 10 and len(b) >= 10:
            _, p = mann_whitney(a, b); d = cliffs_delta(a, b)
            print(f"  {band:14} AI med {a.median():7.2f} (n={len(a)}) | non med {b.median():7.2f} (n={len(b)}) "
                  f"| delta {d:+.3f} {interp_delta(d)} p {p:.4f}")

    # persist a machine-readable summary
    out = ROOT / "data" / "analysis_summary.json"
    summary = {"n_prs": len(df), "n_ai": int(len(ai)), "n_non": int(len(non)),
               "repos": int(df.repo.nunique()),
               "ai_median_size": float(ai.pr_size.median()), "non_median_size": float(non.pr_size.median())}
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nsummary -> {out}")


if __name__ == "__main__":
    main()
