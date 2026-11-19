# ai-code-review-cost

What review outcomes are associated with explicitly AI-tagged pull requests on public GitHub projects?

This repository contains the collection and analysis code for an observational empirical software engineering study. The source data comes from public GH Archive event files. Row-level datasets stay local because they are large and because they retain public repository and pull-request identifiers. Small manifests and aggregate results document the exact inputs and outputs.

Review cost is not observed. GH Archive records no reviewer minutes, no attention, and no value of review discussion. Every measure here is a proxy for observable workflow burden, and the study reports associations rather than the causal effect of giving a developer an assistant for the same task.

## Study design

The study uses two samples.

### Review outcomes

The review sample draws one fixed 12-hour block per month from October 2024 through September 2025, 06:00 through 17:59 UTC on the 15th. The driver requests 156 hourly archives: 144 analysis hours plus one following push-only context hour per block, read only for merge-commit evidence. All 156 requested files downloaded and parsed successfully. Pull-request and review events from context hours are excluded.

Primary analysis includes only pull requests that have qualifying head or merge SHA evidence, are opened by a GitHub account whose type is `User`, and can be classified by the same timing rule in both groups.

The primary observable review outcome is review incidence: whether a pull request receives at least one qualifying formal review event, meaning a pre-merge review submitted by a non-author account whose GitHub type is not `Bot`, deduplicated by review ID. Review incidence, review latency, and review rounds are computed only for pull requests opened and merged inside the same sampled block, because a 12-hour block cannot contain reviews that fall outside it. Latency is further conditional on receiving a qualifying review.

Final review population:

- 730,671 merged pull requests collected
- 660,215 with qualifying push evidence
- 195,032 bot-authored records excluded, and no record with a missing author type
- 465,183 human-authored pull requests analyzed across 220,261 repositories
- 1,113 AI-tagged and 464,070 comparison pull requests
- 345,010 in the contained formal-review subset, 826 tagged and 344,184 comparison

### Revert outcome

The revert sample covers Aug. 1 through Aug. 30, 2025 UTC, ending at the start of Aug. 31. All 720 requested hourly archives downloaded and parsed successfully.

The primary endpoint asks whether a qualifying pull request receives explicit revert evidence within exactly 14 days after merge. The collector accepts same-repository `This reverts commit <sha>` evidence pushed to the original target branch and explicit references in a merged revert pull request on that branch. It keeps the earliest evidence timestamp, scopes short SHA prefixes to the repository and branch, excludes a revert pull request's own number, and deduplicates repeated merge events. Inference is restricted to records merged before Aug. 17 so every retained record has a complete 14-day window.

Final revert cohort:

- 2,923,316 merged pull requests collected
- 2,435,960 with qualifying push evidence
- 917,642 bot-authored linkable records excluded, leaving 1,518,318 opened by `User` accounts
- 708,515 right-censored records excluded
- 809,803 pull requests in the cohort, 12,689 AI-tagged and 797,114 comparison

## AI tag

A pull request is AI-tagged when its qualifying head or merge commit carries an explicit, named assistant identity. The detector accepts a Claude co-author line with Anthropic's no-reply address or a `Generated with Claude Code` line, a Copilot co-author line with a recognized GitHub Copilot address, a Cursor or Cursoragent co-author line, a Devin co-author line with the Devin integration address, and a Codex co-author line with the OpenAI Codex address. Nothing else counts. Bare vendor words are rejected, because an early loose detector matched ordinary prose such as a reference to a text cursor, and unnamed bot or agent trailers are not treated as a label.

Head-SHA evidence must appear no later than merge. Merge-SHA evidence may appear within one hour after merge because GitHub records `merged_at` before the public merge-commit PushEvent. This allowance applies equally to both groups.

Tagged records are 0.24 percent of the review sample and 1.57 percent of the revert cohort. In the review sample, Claude appears on 485 records, Copilot on 419, Cursor on 198, and Devin on 14, with a few records carrying two tags and no Codex tag. In the revert cohort, Claude appears on 5,969, Cursor on 4,039, Copilot on 2,700, and Devin on 48, again with overlap and no Codex tag.

The label captures explicit tags rather than all assistant use. Tool defaults, merge strategy, project policy, and developer behavior all affect which assisted changes receive a tag, and assistant use attributed under any other identity is unlabeled.

## Findings

The review sample does not show lower review-stage burden for AI-tagged pull requests.

The crude formal-review gap is large and the adjusted gap is not. Only 45 of 826 contained tagged pull requests, 5.4 percent, have at least one qualifying formal review event, against 48,248 of 344,184 comparison pull requests, 14.0 percent. The crude difference is -8.57 percentage points, with a Newcombe unclustered interval from -9.93 to -6.80 and a repository-clustered interval from -10.36 to -6.79 across 178,049 repositories. Reweighting comparison pull requests to the tagged group's mix shrinks it: -6.78 points by size, -3.81 by month, -3.41 by author association, and -0.72 jointly, where the clustered interval runs from -2.34 to +0.89 and includes zero. Within 238 repositories the Mantel-Haenszel difference is -6.48 points using 311 of 826 tagged records, and adding size, month, and author strata inside repositories leaves 140 tagged records and an interval from -8.52 to +1.68. The gap is not concentrated in a few projects, since tagged records span 644 repositories, the five largest hold 3.6 percent of them, and dropping those five together leaves -8.36 points. The raw drop is statistically clear and largely tracks change size, timing, and author role, and the data cannot isolate a remaining tag effect.

The other measures point the same way. Tagged pull requests merge more slowly, with a median of 0.308 hours against 0.066 and a Cliff's delta of +0.210, a small effect. Across 381 equally weighted repositories the median difference is +0.045 hours, about three minutes, with an interval from +0.009 to +0.087 and an exact sign-test p below 0.001. Among records with a qualifying review, tagged pull requests wait a median 0.373 hours for the first review against 0.233, a delta of +0.190, but this rests on 45 tagged records and the 25-repository check gives a median difference of 0.005 hours and a sign-test p of 1, so latency is inconclusive. Review rounds have a median of zero in both groups and a negligible delta of -0.086, which mostly restates lower incidence. The largest differences are size and activity, not timing: commits +0.337, changed lines +0.360, files changed +0.236, issue comments +0.208, and review comments +0.346. Tool-tag results differ materially, with merge-time deltas of +0.356 for Copilot, +0.177 for Claude, and -0.034 for Cursor, so the aggregate cannot be read as one assistant's effect.

The fixed 14-day revert comparison is uncertain.

- AI-tagged rate: 0.363 percent, 46 of 12,689
- Comparison rate: 0.277 percent, 2,211 of 797,114
- Difference: +0.085 percentage points
- Newcombe 95 percent interval: -0.006 to +0.206 percentage points
- Pooled z-test: p = 0.071
- Random-label simulation, 20,000 draws: p = 0.079 against 35.4 expected tagged reverts
- Repository-clustered interval across 249,519 repositories: -0.044 to +0.215 percentage points, p = 0.198
- Size-standardized difference: +0.159 percentage points, reported without an interval

The intervals include zero and still permit a meaningful increase. The result establishes neither equal revert rates nor a reliable difference. Sixteen eligible records carry explicit revert evidence after day fourteen and are counted as non-reverts for the primary endpoint.

## Analysis

The review analysis treats review incidence as the primary observable outcome and reports the crude difference, a Newcombe unclustered interval, repository-clustered sandwich inference, direct standardization to the tagged group's size, month, and author-association mix, repository-stratified and repository-plus-covariate Mantel-Haenszel risk differences with Sato variance, tagged-cluster concentration, and leave-top-repository checks. Row-level measures report medians, Mann-Whitney tests with tie correction, Cliff's delta with the Romano magnitude convention, percentile-bootstrap intervals from 1,000 resamples, Benjamini-Hochberg adjusted p-values, size and month checks, equal-repository comparisons with 10,000 repository resamples and an exact sign test, and descriptive tool-tag results. At this sample size a negligible difference can produce a very small p-value, so the adjusted p-values stay descriptive.

The revert analysis reports Newcombe intervals, a pooled two-proportion test, size standardization, author-association strata with multiplicity correction, an exact within-repository sign test, a hypergeometric random-label simulation, and repository-clustered sandwich inference.

## Reproduce

See [REPRODUCE.md](REPRODUCE.md) for pinned dependencies, commands, validation checks, and expected artifacts.

Key commands:

```bash
python3 code/gha_run_year.py --out data/gha_prs.csv
python3 code/analyze_gha.py
python3 code/validate_spotcheck.py --coordinates data/validation_spotcheck_coordinates.json --target 10
python3 code/gha_reverts.py --start 2025-08-01 --days 30 --out data/gha_reverts.csv
python3 code/robustness.py
```

The coordinate file passed to `validate_spotcheck.py` is private. An independent reproducer can omit `--coordinates` to draw seeded candidates from a rebuilt CSV, as REPRODUCE.md describes, and results may differ when repositories are deleted or made private.

Run the full regression suite, which holds 83 tests:

```bash
python3 -m unittest -q \
  test_detect_ai_commits.py \
  test_gharchive_collect.py \
  test_gha_run_year.py \
  test_analyze_gha.py \
  test_gha_reverts.py \
  test_robustness.py \
  test_make_effect_figure.py \
  test_validate_spotcheck.py
```

## Transparency artifacts

The repository includes:

- `data/gha_prs.csv.manifest.json`
- `data/analysis_real.txt`
- `data/analysis_real_summary.json`
- `data/validation_spotcheck_summary.json`
- `data/gha_reverts.csv.manifest.json`
- `data/robustness.txt`
- `data/robustness_summary.json`

Each manifest records every source hour and binds the derived CSV to its exact byte size and SHA-256 digest. The analyses refuse an incomplete or mismatched manifest.

Field extraction is checked against GitHub's public per-pull-request endpoint on a fixed fixture of ten records, five tagged and five comparison, all opened by `User` accounts. The coordinates were selected from the final CSV for continued public availability and recorded by hash. The set was revised once before the final run to require `User` authors, which replaced two of the ten records, and the earlier set is archived privately. This is a bounded fixture rather than an inferential random sample. Because availability was a selection criterion, the fact that all ten records were reachable is expected and is not evidence about the wider sample. Merge timestamp, author type, commits, changed lines, and files changed matched on every one. No merge-time comment count exceeded its current value, and one record's current issue-comment count is higher than its merge-time snapshot, which is ordinary post-merge discussion. The strict label reproduced for all five tagged records, two Claude, two Copilot, and one Devin, each matching the recorded tool. The check covers closed-event fields and label reproduction on those records. It does not estimate label precision or recall, does not test formal review-event capture, and cannot show that the event stream is complete.

Row-level CSV files and the detailed validation coordinates stay out of version control. The coordinates link repository and pull-request identifiers to the derived tag, so they are referenced publicly only by hash, and the released validation summary is a redacted aggregate carrying counts, field names, hashes, and match flags with no repository or pull-request identifiers.

## Limits

- Review cost is unobserved, and every measure here is a proxy for observable workflow burden.
- The design is observational. No result is a causal effect of assistant use.
- Explicit named tags miss untagged use and assistant work attributed under another identity.
- Tag visibility varies with merge strategy, pull-request age, and event coverage.
- Monthly review blocks favor fast workflows and do not represent the full year. Four of the twelve blocks fall on a weekend, and the UTC interval underweights US working hours.
- Linkability falls from 94.9 percent in October 2024 to 64.1 percent in September 2025, mostly from June onward, where most tagged records sit. The revert window is steadier, between 82.4 and 87.4 percent by merge week.
- Formal review events do not capture all human review work. Most changes in both groups record no qualifying review, which can mean no review, review through comments, or review outside GitHub.
- Closed-event comment totals cannot exclude automation.
- Explicit revert evidence misses silent rollbacks and can still misread unusual prose, so the direction of measurement error is unknown. Reverts are rare, 2,257 across the cohort, which limits precision.
- Tag prevalence differs between the two collections, 0.24 against 1.57 percent, so neither set of estimates carries to the other.
- Public open source differs from private and enterprise development.
- GitHub reduced public event payloads after the study period, so this labeling method cannot be assumed to work on newer events.

## Provenance of this repository

The analyses here use observed public event data. The repository contains no synthetic observations and no AI-generated empirical results. An assistant was used for drafting, code inspection, test development, consistency checks, and figures, and the author reviewed every change and reran the analyses.

## License

MIT. See [LICENSE](LICENSE).

## Sources

- ⚠️ External link, [GH Archive](https://gharchive.org/), accessed 2026-11-19
- ⚠️ External link, [GitHub Events API payload changes](https://github.blog/changelog/2025-08-08-upcoming-changes-to-github-events-api-payloads/), accessed 2026-11-18
- ⚠️ External link, [GitHub REST pull-request API](https://docs.github.com/en/rest/pulls/pulls), accessed 2026-11-19
