# ai-code-review-cost

An empirical study of whether AI-assisted pull requests cost or save reviewer effort on public
open-source projects. This repository is the reproducibility package: the data-collection and
analysis code, the collected dataset (or the exact queries to regenerate it), and the methods log.

## Research question

On public open-source projects, do AI-assisted pull requests differ from non-AI pull requests in
review cost and downstream quality, and what should practitioners conclude?

## Method (in brief)

Within the same repositories, compare **AI-assisted PRs** against **non-AI PRs** on five measures:

1. Review time (PR opened or marked ready, to first approval or merge)
2. Revision rounds (review-and-update cycles before merge)
3. Rework within 30 days of merge (follow-up commits to the same lines)
4. Reverts (whether the merged change was later reverted)
5. Time to merge

"AI-assisted" is detected only from explicit, machine-written commit trailers (see
`code/detect_ai_commits.py`). This is a deliberate under-count proxy: many AI-assisted PRs carry no
marker.

Repository is treated as a covariate, so no single repo needs an even AI/non-AI split. Statistics
use medians and non-parametric tests with effect sizes, not means.

## Study population (stated honestly)

The population is **public repos that openly tag AI-assisted commits**. That is a specific,
defensible population, not "all software." Tagging is voluntary, so untagged AI use is invisible
and taggers may differ systematically from non-taggers. This selection bias is addressed directly
in the paper's Threats to Validity. The corpus is assembled from two sources, then verified:
popular AI-adjacent projects (repository search) and a broad commit search, filtered by a star
gate and by real trailer density.

## Honesty rules

- Only real, verifiable data from public repositories. No synthesized or imputed results.
- Every number is reproducible from the code and the recorded commit SHAs / queries.
- Where a measurement is a proxy, the limitation is stated plainly.

## Status

Early. The AI-signal detector is built and validated on a pilot repo. Corpus assembly (two-source
discovery) and PR-metric collection are in progress. Raw data is not published until it is validated.

## Layout

```
code/      collection + analysis scripts
data/      per-PR dataset + corpus list (gitignored until validated)
REPRODUCE.md   exact steps to regenerate every number
methods-log.md decisions, the AI-signal definition, final counts, validity notes
```

## License

MIT. See LICENSE.
