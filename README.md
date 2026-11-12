# ai-code-review-cost

Do AI-assisted pull requests cost or save reviewer effort on public open-source projects?

This repository holds the code and the reproducible method for an empirical study of that question.
It is built to be checked, not trusted. Every number comes from public data, and the scripts here
rebuild that data from scratch.

## Question

AI coding assistants are sold on speed. Most public evidence stops at the moment a suggestion is
accepted. This study looks one step later, at code review, and asks whether AI-assisted pull requests
are cheaper or faster to review than other pull requests, and whether they are reverted more often.

## Data source

We use GH Archive (gharchive.org), the public, timestamped record of GitHub's event stream. It is the
standard dataset for this kind of work because it is public, vendor-neutral, and fully reproducible by
anyone who downloads the same hours.

Note on method history: an earlier version pulled data from the live GitHub API. We moved off that path
after finding the aggregate counts it returned in our environment were not reliable, and switched to GH
Archive, whose records we validated against known ground truth. The canonical reproduction uses the GH
Archive scripts listed below. The older API-discovery script (`discover_corpus.py`) is kept for history
only and is not part of the current method.

## Method

- **AI label:** a pull request is AI-assisted when one of its commits carries an explicit assistant
  trailer (`Co-authored-by: Claude/Copilot/Cursor/Devin/Aider/Codex`, `Generated with Claude Code`).
  The detector (`code/detect_ai_commits.py`) requires the trailer form, not a loose keyword, after an
  early version mismatched ordinary prose. The match is conservative: it keys on a pull request's tip
  or merge commit, so it undercounts rather than overcounts AI use.
- **Metrics:** time to merge, review latency, review rounds, commits, pull request size, files changed,
  comments, and (separately) revert rate.
- **Controls:** within-repository comparison, size stratification, month matching.
- **Statistics:** Mann-Whitney U (normal approximation with tie correction), Cliff's delta with
  bootstrap confidence intervals, Benjamini-Hochberg correction, plus a permutation test for reverts.

## Findings (honest summary)

On 2.9 million public merged pull requests, the result is a near-null:

- AI-assisted pull requests **do not merge faster** and do not draw fewer review rounds.
- They are **larger and carry more commits and comments** (small effects).
- Their **revert rate does not differ reliably** from other pull requests (z-test p = 0.068,
  permutation p = 0.070, and the gap fails a size control).

The speed promised for AI assistance does not show up at the review stage in this data. This is a
near-null, reported as such. It neither proves AI helps review nor that it hurts.

## Reproduce

```
# 1. review-cost dataset: 12 monthly windows, Oct 2024 - Sep 2025
python3 code/gha_run_year.py

# 2. review-cost analysis
python3 code/analyze_gha.py

# 3. downstream reverts: one contiguous 30-day window
python3 code/gha_reverts.py --start 2025-08-01 --days 30 --out data/gha_reverts.csv

# 4. revert robustness (two-proportion, size/author control, permutation)
python3 code/robustness.py
```

Raw datasets are large and are not committed. The small result artifacts
(`data/robustness.txt`, `data/analysis_real_summary.json`) are included for transparency.

## Honesty rules

- Only real public data. No fabricated metrics or results.
- A null result is reported as a null.
- Conservative choices (strict AI label, tip-commit match) bias toward finding no difference, so any
  difference that survives is real and any null is read as genuine.
- Known limits are stated in the paper's threats-to-validity section: the label sees only openly tagged
  AI use, review events are sparse, reverts undercount silent rollbacks, same-file 30-day rework is not
  measurable from GH Archive, and public open source is not private or enterprise development.

## License

MIT. See `LICENSE`.
