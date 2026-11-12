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
from pathlib import Path

import numpy as np
import pandas as pd

from analyze import mann_whitney, cliffs_delta, boot_ci_delta, bh, interp_delta

ROOT = Path(__file__).resolve().parent.parent
PRS = ROOT / "data" / "gha_prs.csv"
OUT = ROOT / "data" / "analysis_real.txt"
METRICS = ["time_to_merge_h", "review_latency_h", "review_rounds",
           "commits", "pr_size", "files_changed", "comments", "review_comments"]
PRIMARY = ["time_to_merge_h", "review_latency_h", "review_rounds", "comments"]


def main():
    np.random.seed(0)
    out = []
    def p(*a):
        line = " ".join(str(x) for x in a)
        out.append(line)
        print(line, flush=True)

    df = pd.read_csv(PRS)
    for c in METRICS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["is_ai"] = pd.to_numeric(df["is_ai"], errors="coerce").fillna(0).astype(int)
    df["linkable"] = pd.to_numeric(df["linkable"], errors="coerce").fillna(0).astype(int)

    total_rows = len(df)
    df = df[df.linkable == 1].copy()   # only PRs we can validly classify
    ai = df[df.is_ai == 1]; non = df[df.is_ai == 0]
    p("=" * 78)
    p("CHECKPOINT 3 — REAL DATA (GH Archive, Oct 2024 - Sep 2025)")
    p("=" * 78)
    p(f"rows total {total_rows:,} | linkable (analyzed) {len(df):,} | AI {len(ai):,} | non-AI {len(non):,}")
    p(f"distinct repos: {df.repo.nunique():,}")
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
        a = ai[m].dropna().values; b = non[m].dropna().values
        _, pv = mann_whitney(a, b)
        d = cliffs_delta(a, b)
        rows.append((m, pv, d, a, b))
    adj = bh([r[1] for r in rows])
    for (m, pv, d, a, b), ap in zip(rows, adj):
        lo, hi = boot_ci_delta(a, b, 200)
        sig = "SIG" if ap < 0.05 else "ns"
        p(f"  {m:17} AI med {np.median(a):9.2f} | non med {np.median(b):9.2f} | "
          f"delta {d:+.3f} [{lo:+.2f},{hi:+.2f}] {interp_delta(d):10} p_adj {ap:.4g} {sig}")
    p("")

    p("--- WITHIN-REPO (each repo its own control; repos with >=5 AI & >=5 non-AI) ---")
    for m in PRIMARY:
        diffs = []
        for repo, g in df.groupby("repo"):
            a = g[g.is_ai == 1][m].dropna(); b = g[g.is_ai == 0][m].dropna()
            if len(a) >= 5 and len(b) >= 5:
                diffs.append(a.median() - b.median())
        if diffs:
            diffs = np.array(diffs)
            p(f"  {m:17} repos={len(diffs):3d} | median per-repo (AI-nonAI) = {np.median(diffs):+.2f} "
              f"| AI lower in {100*(diffs<0).mean():.0f}% of repos")
        else:
            p(f"  {m:17} too few repos with both groups >=5")
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
    for mo, g in df.groupby("month"):
        a = g[g.is_ai == 1].time_to_merge_h.dropna(); b = g[g.is_ai == 0].time_to_merge_h.dropna()
        if len(a) >= 10 and len(b) >= 10:
            mdiffs.append(a.median() - b.median())
    if mdiffs:
        p(f"  months={len(mdiffs)} | median per-month (AI-nonAI) = {np.median(mdiffs):+.2f}h "
          f"| AI faster in {100*(np.array(mdiffs)<0).mean():.0f}% of months")

    OUT.write_text("\n".join(out))
    (ROOT / "data" / "analysis_real_summary.json").write_text(json.dumps({
        "rows_total": int(total_rows), "linkable": int(len(df)),
        "ai": int(len(ai)), "non_ai": int(len(non)), "repos": int(df.repo.nunique())}, indent=2))
    p(f"\n[report -> {OUT}]")


if __name__ == "__main__":
    main()
