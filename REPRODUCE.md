# Reproduce the Study

This repository rebuilds both analysis samples from public GH Archive event files. Raw hourly archives are processed and deleted one at a time. The generated row-level CSV files remain local because they are large.

## Reference environment

The final validation used:

- Python 3.14.7
- NumPy 2.5.0
- pandas 3.0.3
- Pillow 12.2.0
- `curl` with HTTPS support

Create an isolated environment and install the pinned packages:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run the tests

```bash
.venv/bin/python -m unittest -q \
  test_detect_ai_commits.py \
  test_gharchive_collect.py \
  test_gha_run_year.py \
  test_analyze_gha.py \
  test_gha_reverts.py \
  test_robustness.py \
  test_make_effect_figure.py \
  test_validate_spotcheck.py
```

The tests cover event-window boundaries, complete-history containment, repository-level comparisons, conservative revert matching, linkability, censoring, interrupted-run preservation, random-label simulation, cluster-aware inference, and figure generation.

## Build the review sample

```bash
.venv/bin/python code/gha_run_year.py --out data/gha_prs.csv
```

The driver requests 156 hourly archives. It samples twelve analysis hours on the 15th day of each month from October 2024 through September 2025, from 06:00 to 17:59 UTC, plus the 18:00 push-only context hour for merge-SHA linkage. Pull-request and review events from context hours are excluded. Every hour must download, pass gzip validation, and contain valid JSON. A failure stops the run and preserves the previous output. Head-SHA evidence must occur no later than merge. Merge-SHA evidence has a one-hour grace for GitHub event sequencing, applied equally to both groups. The primary analysis includes only pull requests whose author type is `User`, reporting bot and missing types separately. Formal-review metrics count deduplicated reviews submitted no later than merge by non-author users whose type is not `Bot`. All-review counts remain in the local CSV as diagnostics.

A successful run creates:

- `data/gha_prs.csv`, the local row-level sample
- `data/gha_prs.csv.manifest.json`, the public hour and count manifest

Run the review analysis:

```bash
.venv/bin/python code/analyze_gha.py
```

This creates:

- `data/analysis_real.txt`
- `data/analysis_real_summary.json`

The review analysis treats the presence of at least one qualifying formal review event as the primary observable review outcome. It reports the crude difference, a Newcombe unclustered interval, repository-clustered inference, tagged-distribution standardization by size, month, and author association, repository-stratified Mantel-Haenszel sensitivity, concentration, and leave-top-repository checks. Review latency remains conditional on receiving a qualifying review and is reported descriptively.

Validate five explicitly AI-tagged and five comparison records against GitHub's public pull-request API. The final audit uses a private fixed-coordinate fixture chosen from the final User-authored CSV for continued public availability and bounded commit checks:

```bash
.venv/bin/python code/validate_spotcheck.py \
  --coordinates data/validation_spotcheck_coordinates.json \
  --target 10
```

The private coordinate file is withheld because it links repository and pull-request identifiers to the derived tag. Its SHA-256 is recorded in the redacted summary as a commitment. SciPub and reviewers can receive the detailed fixture on request. An independent reproducer can omit `--coordinates` to draw seeded candidates from a rebuilt final CSV, but results may differ when repositories are deleted or made private.

The validator requires linkable records from `User` accounts and binds the input CSV to its complete collector manifest. Merge time, author type, commit count, changed lines, and changed-file count must match exactly. Current issue-comment and review-comment counts may exceed their merge-time snapshots because GitHub permits later comments; decreases are reported as mismatches. For each tagged coordinate, the validator checks public pull-request commits and then the merge commit when needed. The expected strict label must appear under the same detector rules. Commit messages are not retained.

This creates:

- `data/validation_spotcheck.json`, the private detailed result with per-pull-request coordinates
- `data/validation_spotcheck_summary.json`, the public redacted aggregate without repository or pull-request identifiers

Generate the figure from the JSON summary:

```bash
.venv/bin/python code/make_effect_figure.py
```

## Build the revert sample

```bash
.venv/bin/python code/gha_reverts.py \
  --start 2025-08-01 \
  --days 30 \
  --out data/gha_reverts.csv
```

The collector requests 720 hourly archives covering Aug. 1 through Aug. 30, 2025 UTC. It processes hours newest first so it can match earlier pushes to pull requests without retaining every public commit. Head-SHA evidence must appear no later than merge. Merge-SHA evidence may appear within one hour after merge because GitHub emits the merge-commit push after recording `merged_at`. The identical rule applies to tagged and comparison labels. Revert evidence comes from same-repository git-revert SHAs pushed to the original target branch or explicit references in a merged revert pull request on that branch. The earliest evidence timestamp is stored for each pull request. Primary inference requires a linkable pull request opened by an account whose GitHub type is `User`; Bot and missing author types are excluded and reported.

A successful run creates:

- `data/gha_reverts.csv`, the local row-level sample
- `data/gha_reverts.csv.manifest.json`, the public hour and count manifest

Run the censored revert analysis:

```bash
.venv/bin/python code/robustness.py
```

This creates:

- `data/robustness.txt`
- `data/robustness_summary.json`

The default inferential cohort includes linkable pull requests merged before Aug. 17. Every included record has a complete 14-day observation period. A revert counts only when its earliest explicit evidence falls no later than fourteen days after that pull request merged.

## Validate the outputs

Check that each manifest reports equal requested and completed hour counts:

```bash
python3 - <<'PY'
import json
for path in [
    "data/gha_prs.csv.manifest.json",
    "data/gha_reverts.csv.manifest.json",
]:
    manifest = json.load(open(path))
    assert manifest["status"] == "complete"
    assert manifest["requested_hours"] == manifest["completed_hours"]
    print(path, manifest["completed_hours"])
PY
```

Compare regenerated summaries with the small transparency artifacts in version control. Differences should be investigated rather than accepted silently.

## Superseded scripts

`discover_corpus.py`, `pr_collect.py`, and the local API path are retained only as method history. They are not part of the paper's reproduction flow.

## Sources

- ⚠️ External link: [GH Archive](https://gharchive.org/) accessed 2026-11-18
