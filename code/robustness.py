#!/usr/bin/env python3
"""Robustness checks on the revert finding (AI vs non-AI), streamed from gha_reverts.csv (memory-safe).

Answers the questions that decide whether the finding survives:
  1. Is the AI-vs-non revert difference statistically real? (two-proportion z-test + 95% CI)
  2. Does it survive controlling for PR SIZE? (AI PRs are bigger; big PRs may revert more anyway)
  3. Does it survive controlling for AUTHOR association?
  4. Within-repo sign test: of powered repos, is AI-higher more than chance? (binomial)

Cohort = PRs merged in the first 16 days of the window (>=14d forward observation).
"""
import csv
import datetime as dt
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "gha_reverts.csv"
CUTOFF = dt.datetime(2025, 8, 17, tzinfo=dt.timezone.utc)   # first 16 days of a 2025-08-01 window


def band(sz):
    if sz is None:
        return "unknown"
    return "small<=50" if sz <= 50 else "med51-300" if sz <= 300 else "large>300"


def two_prop(r1, n1, r2, n2):
    """r1/n1 = AI, r2/n2 = non. Returns (p1, p2, diff_pp, ci_lo_pp, ci_hi_pp, z, pval)."""
    if n1 == 0 or n2 == 0:
        return (0, 0, 0, 0, 0, 0, 1)
    p1, p2 = r1 / n1, r2 / n2
    diff = p1 - p2
    se_ci = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    pp = (r1 + r2) / (n1 + n2)
    se_z = math.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    z = diff / se_z if se_z else 0
    pval = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return (p1 * 100, p2 * 100, diff * 100, (diff - 1.96 * se_ci) * 100, (diff + 1.96 * se_ci) * 100, z, pval)


def binom_sign(k, n, p=0.5):
    """Two-sided exact-ish binomial p for k of n above chance (normal approx for large n)."""
    if n == 0:
        return 1.0
    mu, sd = n * p, math.sqrt(n * p * (1 - p))
    if sd == 0:
        return 1.0
    z = (abs(k - mu) - 0.5) / sd
    return 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))


def main():
    overall = {0: [0, 0], 1: [0, 0]}          # is_ai -> [n, reverts]
    by_size = defaultdict(lambda: {0: [0, 0], 1: [0, 0]})
    by_auth = defaultdict(lambda: {0: [0, 0], 1: [0, 0]})
    byrepo = defaultdict(lambda: {0: [0, 0], 1: [0, 0]})
    rows = 0
    with CSV.open() as f:
        for r in csv.DictReader(f):
            try:
                m = dt.datetime.fromisoformat(r["merged_at"].replace("Z", "+00:00"))
            except Exception:
                continue
            if m >= CUTOFF:
                continue
            ai = 1 if r["is_ai"] == "1" else 0
            rev = 1 if r["reverted"] == "1" else 0
            try:
                sz = float(r["pr_size"]) if r["pr_size"] not in ("", "None") else None
            except Exception:
                sz = None
            rows += 1
            for store, key in ((overall, None), (by_size, band(sz)),
                               (by_auth, r["author_assoc"] or "NONE"), (byrepo, r["repo"])):
                d = store if key is None else store[key]
                d[ai][0] += 1; d[ai][1] += rev

    out = []
    def p(*a): line = " ".join(str(x) for x in a); out.append(line); print(line, flush=True)

    p("=" * 74)
    p("ROBUSTNESS - revert finding (cohort merged first 16 days)")
    p("=" * 74)
    p(f"cohort PRs: {rows:,}")
    a, n = overall[1], overall[0]
    p1, p2, diff, lo, hi, z, pv = two_prop(a[1], a[0], n[1], n[0])
    p("")
    p("1) OVERALL two-proportion test")
    p(f"   AI {p1:.3f}% ({a[1]}/{a[0]})  vs  non-AI {p2:.3f}% ({n[1]}/{n[0]})")
    p(f"   diff = {diff:+.3f} pp  95% CI [{lo:+.3f}, {hi:+.3f}]  z={z:.2f}  p={pv:.2e}  "
      f"{'SIGNIFICANT' if pv < 0.05 else 'not significant'}")
    sig_excl_zero = (lo > 0 or hi < 0)
    p(f"   -> CI {'excludes' if sig_excl_zero else 'includes'} zero")

    p("")
    p("2) SIZE-CONTROLLED (does AI>non survive within size bands?)")
    for b in ["small<=50", "med51-300", "large>300"]:
        d = by_size[b]; a, n = d[1], d[0]
        if a[0] >= 30 and n[0] >= 30:
            p1, p2, diff, lo, hi, z, pv = two_prop(a[1], a[0], n[1], n[0])
            p(f"   {b:11} AI {p1:.3f}% (n={a[0]}) vs non {p2:.3f}% (n={n[0]}) | "
              f"diff {diff:+.3f}pp [{lo:+.2f},{hi:+.2f}] p={pv:.2e} {'SIG' if pv<0.05 else 'ns'}")

    p("")
    p("3) AUTHOR-CONTROLLED (top associations)")
    for auth in sorted(by_auth, key=lambda k: -sum(by_auth[k][i][0] for i in (0, 1)))[:4]:
        d = by_auth[auth]; a, n = d[1], d[0]
        if a[0] >= 30 and n[0] >= 30:
            p1, p2, diff, lo, hi, z, pv = two_prop(a[1], a[0], n[1], n[0])
            p(f"   {auth:12} AI {p1:.3f}% (n={a[0]}) vs non {p2:.3f}% (n={n[0]}) | "
              f"diff {diff:+.3f}pp p={pv:.2e} {'SIG' if pv<0.05 else 'ns'}")

    p("")
    p("4) WITHIN-REPO sign test (repos with >=20 AI & >=20 non-AI)")
    higher = total = 0
    for repo, d in byrepo.items():
        a, n = d[1], d[0]
        if a[0] >= 20 and n[0] >= 20:
            total += 1
            if (a[1] / a[0]) > (n[1] / n[0]):
                higher += 1
    if total:
        pv = binom_sign(higher, total)
        p(f"   powered repos: {total} | AI revert-rate higher in {higher} "
          f"({100*higher/total:.0f}%) | binomial p={pv:.3f} {'SIG' if pv<0.05 else 'ns'}")

    (ROOT / "data" / "robustness.txt").write_text("\n".join(out))
    p("")
    p("[saved -> data/robustness.txt]")


if __name__ == "__main__":
    main()
