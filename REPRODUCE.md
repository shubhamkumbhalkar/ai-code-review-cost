# Reproduce

1. Detect AI-assisted commits in a cloned repo:
       git clone --filter=blob:none <repo-url>
       python3 code/detect_ai_commits.py <repo-dir>
2. (In progress) Corpus assembly: list of repos + commit SHAs used.
3. (In progress) PR-metric collection via GitHub API (requires a read-only token).
4. (In progress) Analysis: medians, non-parametric tests, effect sizes, sensitivity.

Every number in the paper traces to a step here. Raw data lands in data/ once validated.
