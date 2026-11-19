# Methods Log

## Research question

What observable review-stage and 14-day revert outcomes are associated with explicitly AI-tagged pull requests on public GitHub projects?

The design is observational. It does not estimate the causal effect of giving the same developer an assistant for the same task. Review cost itself is never observed, because the event stream records no reviewer minutes, attention, or discussion value.

## Data source

The study uses public GH Archive hourly event files. An earlier attempt used aggregate GitHub API data, but implausible responses in the local environment made that path unsuitable for the main dataset. The final collectors use GH Archive for reconstruction and the public per-pull-request API only for a small field-validation fixture.

## Explicit AI tag

A commit is tagged only when it carries an explicit, named assistant identity. The detector in `code/detect_ai_commits.py` accepts five forms:

- a Claude co-author line with Anthropic's no-reply address, or a `Generated with Claude Code` line
- a Copilot co-author line with a recognized GitHub Copilot address
- a Cursor or Cursoragent co-author line
- a Devin co-author line with the Devin integration address
- a Codex co-author line with the OpenAI Codex address

Nothing else counts. Bare vendor words are rejected because pilot testing produced false positives such as ordinary references to a text cursor. The module also carries a `generic_agent` pattern for unnamed bot or agent trailers, and `classify()` excludes it, so unnamed trailers never produce a label. No Aider pattern exists, and no Aider tag appears in either sample.

The label has high precision as its goal and incomplete coverage as a known limit. It can miss untagged use, assistant work attributed under another identity, and assistant commits outside the qualifying head or merge SHA. Tool defaults, merge strategy, repository policy, and developer behavior all affect which assisted changes receive a tag.

## Event-sequencing rule

GitHub records `merged_at` before the merge-commit PushEvent reaches the public event stream. An overstrict validation run that rejected all post-merge evidence removed more than half of otherwise linkable records.

The final rule is:

- head-SHA evidence must occur no later than merge
- merge-SHA evidence may occur no later than one hour after merge
- the same rule applies to tagged and comparison pull requests

Tests accept merge-SHA evidence at 59 minutes and reject evidence at one hour plus one second. The review driver requests one following push-only context hour per block, and the reverse revert collector retains push metadata from the immediately following hour, so the rule works across hour boundaries.

## Review sample

The collector uses the 15th day of each month from October 2024 through September 2025, from 06:00 through 17:59 UTC.

The definitive rebuild requested 156 hours and completed 156: 144 analysis hours plus 12 push-only context hours, one per block. Pull-request and review events from context hours are discarded.

- 144 of 144 analysis hours, 14,265,206,740 compressed bytes, 29,911,331 source events
- 12 of 12 context hours, 1,215,200,737 compressed bytes, 2,553,646 source events
- 156 hours combined, 15,480,407,477 compressed bytes, 32,464,977 source events
- 730,671 merged pull requests
- 660,215 pull requests with qualifying push evidence
- 1,549 tagged linkable records before the author filter

The final CSV is 90,923,695 bytes. SHA-256:

`85b2e73df0c9f930dfd7024e6482277a0ae41cba6d7bae5bf0617b2394bf9f27`

### Human author and review rules

Primary analysis requires pull-request author type `User`. The linkable sample contains:

- 465,183 confirmed User-authored pull requests across 220,261 repositories
- 195,032 bot-authored pull requests excluded from primary analysis
- zero records with a missing author type

Formal review measures use only pull requests opened and merged within the same sampled block. A formal review qualifies when its ID is unique within the pull request, its timestamp is no earlier than pull-request creation, its timestamp is no later than merge, the reviewer is not the pull-request author, and the reviewer type is not `Bot`.

An intermediate rebuild exposed eleven negative review latencies caused by review timestamps before the reported creation time. The final creation-to-merge boundary removes them, and the definitive CSV records no negative review latency.

### Review analysis population

- 465,183 confirmed human-authored pull requests
- 1,113 AI-tagged, 0.24 percent, and 464,070 comparison
- 345,010 in the contained formal-review subset, 74.2 percent of each group
- 826 tagged and 344,184 comparison records in the contained subset
- 45 tagged records with a qualifying review, 5.4 percent
- 48,248 comparison records with a qualifying review, 14.0 percent
- tool counts in the primary sample: Claude 485, Copilot 419, Cursor 198, Devin 14, Codex 0, with a few records carrying two tags
- tool counts in the contained subset: Claude 380, Copilot 264, Cursor 179, Devin 3

### Key review inference

Review incidence is the primary observable review outcome. The crude difference is -8.570 percentage points, with a Newcombe unclustered interval from -9.927 to -6.802 and a pooled p of 1.34e-12. Repository-clustered sandwich inference across 178,049 repositories gives the same point estimate with an interval from -10.355 to -6.785 and p = 4.91e-21.

Adjustment moves the estimate toward zero:

| Comparison | Tagged records used | Difference, points | 95 percent interval |
|---|---:|---:|---|
| Crude, repository-clustered | 826 | -8.570 | -10.355 to -6.785 |
| Standardized by size | 826 | -6.782 | -8.486 to -5.078 |
| Standardized by month | 826 | -3.814 | -5.493 to -2.134 |
| Standardized by author association | 826 | -3.409 | -5.185 to -1.633 |
| Standardized jointly | 826 | -0.724 | -2.342 to +0.894 |
| Mantel-Haenszel within 238 repositories | 311 | -6.475 | -10.117 to -2.834 |
| Mantel-Haenszel within 121 repository-covariate strata | 140 | -3.422 | -8.520 to +1.677 |

Standardization retains all 826 tagged records. The repository-stratified estimate retains 311, or 37.7 percent, at CMH p = 0.000436. The repository-plus-covariate estimate retains 140, or 16.9 percent, at CMH p = 0.179. Tagged records span 644 repositories, 481.8 effective clusters, the five largest hold 3.6 percent of tagged records, and dropping those five together leaves a crude difference of -8.365 points. The crude drop is clear, the adjusted estimate is not, and the data cannot isolate a remaining tag effect.

Row-level Cliff's delta estimates:

- merge time: +0.210, median 0.308 against 0.066 hours
- first qualifying review latency: +0.190, median 0.373 against 0.233 hours, on 45 tagged records
- review rounds: -0.086, median zero in both groups
- commits: +0.337, median 3 against 1
- changed lines: +0.360, median 292 against 62
- files changed: +0.236, median 6 against 3
- issue comments: +0.208, median zero in both groups
- review comments: +0.346, median zero in both groups

The commit, changed-line, and review-comment effects are medium under the Romano convention, merge time, latency, files changed, and issue comments are small, and review rounds is negligible. All eight Benjamini-Hochberg adjusted p-values fall below 0.05, which at this scale says little about practical importance.

Equal-repository merge-time analysis covers 381 projects. The median tagged-minus-comparison difference is +0.045 hours with a percentile interval from +0.009 to +0.087. After excluding five ties, 221 projects are higher and 155 lower, at an exact sign-test p of 0.00078. The latency check has only 25 qualifying repositories, a median difference of 0.005 hours, an interval from -0.259 to +0.419, and a sign-test p of 1, so latency is inconclusive.

Tool labels overlap and descriptive results vary materially by tag. Merge-time deltas are +0.356 for Copilot, +0.177 for Claude, and -0.034 for Cursor, and changed-line deltas run from +0.284 for Copilot to +0.436 for Claude. No tool-level causal claim is made.

## Revert sample

The collector processes Aug. 1 through Aug. 30, 2025 UTC in reverse hour order. It parses each compressed hour once, buffers that hour's pull-request and push metadata, processes pull requests before pushes, and retains one following hour of merge-SHA evidence.

The definitive rebuild processed:

- 720 of 720 requested hours
- 59,843,100,820 compressed bytes
- 110,785,484 source events
- 2,923,316 unique merged pull requests
- 2,435,960 pull requests with qualifying push evidence
- 30,802 tagged linkable pull requests before author filtering
- 917,642 bot-authored linkable pull requests
- 6,169 reverted pull requests, of which 5,181 are linkable
- matching index sizes: 149,284 full revert SHAs, 827 short revert SHA prefixes, 7,711 revert pull-request references, 3,230,767 linked SHAs, 1,616,146 unmatched target SHAs

The final CSV is 276,389,264 bytes. SHA-256:

`ebbe8c9f045e927ddb3baf97f510103c5083f2c57b20c95143691cc7003f865b`

### Revert evidence

The collector records the earliest timestamp from either a same-repository commit saying `This reverts commit <sha>` pushed to the original target branch, or a merged revert pull request on that branch whose body explicitly identifies the earlier pull request in the same repository.

Full and short SHA matches are scoped to the repository and the target branch ref. The revert pull request's own number is excluded. Repeated merge events are deduplicated by repository and pull-request number. Opened but unmerged revert pull requests do not count. Evidence timestamped before the merge is rejected.

### Collection runs and the short-SHA fix

Several revert runs did not complete. Complete runs on Nov. 12 and on Nov. 19 at 01:48 UTC produced the legacy and pre-branch-filter datasets listed under superseded artifacts. Separately, runs that stopped between Nov. 18 at 22:53 UTC and Nov. 19 at 07:36 UTC left seven temporary JSONL partials in `data/`, from 6 MB to 785 MB, and a further retry left `data/archive/gha_reverts_retry1_partial_2026-11-19T200315Z.jsonl` at 548,362,650 bytes on Nov. 19 at 20:03 UTC. Because the collector writes to a temporary file and replaces the previous CSV only after every requested hour succeeds, none of these runs overwrote a prior dataset.

The collector file was last modified at 20:52 UTC, and the completed run wrote the final CSV and manifest at 21:51 UTC. The change was a performance fix to short-SHA resolution and keeps the same repository and branch scope. Short prefixes are now stored in a bucket keyed by repository and branch ref, and each pull request resolves candidates by prefix lookup across lengths 7 to 39 inside its own bucket, instead of scanning the global prefix index. `test_short_sha_lookup_does_not_scan_global_index` in `test_gha_reverts.py` substitutes a mapping whose `items()` raises, so a regression that reintroduces a global scan fails the suite.

### Fixed outcome window

Primary inference includes linkable, User-authored pull requests merged before Aug. 17. A revert counts only when its earliest evidence occurs within exactly fourteen days after that pull request merged.

Final cohort:

- 487,356 unlinked and 917,642 bot-authored linkable records excluded before the cohort filter
- 1,518,318 User-authored linkable pull requests in the window
- 708,515 right-censored records merged on or after Aug. 17 excluded
- 809,803 pull requests in the cohort
- 12,689 AI-tagged, 1.57 percent, and 797,114 comparison
- 46 tagged reverts and 2,211 comparison reverts
- 2,257 reverts within the follow-up window across the cohort
- 16 explicit revert signals after day fourteen, counted as non-reverts for the primary endpoint
- cohort tool counts: Claude 5,969, Cursor 4,039, Copilot 2,700, Devin 48, Codex 0, with overlap

### Key revert inference

- tagged rate: 0.363 percent
- comparison rate: 0.277 percent
- difference: +0.085 percentage points
- Newcombe 95 percent interval: -0.006 to +0.206 points
- pooled z-test: z = 1.80, p = 0.071
- 20,000-draw hypergeometric random-label check: observed 46 against 35.4 expected, 1.79 standard deviations, p = 0.079
- repository-clustered interval across 249,519 repositories: -0.044 to +0.215 points, clustered standard error 0.066 points, p = 0.198
- size-standardized difference: +0.159 points, reported without an interval
- size bands: small +0.317 points at p = 0.0016, medium +0.085 at p = 0.386, large -0.019 at p = 0.771, unadjusted for multiplicity
- author-association strata: no difference is significant before or after Benjamini-Hochberg correction, and the smallest unadjusted p is 0.064 for the `NONE` group
- within-repository sign test: 6 eligible repositories, 1 higher, 1 lower, 4 ties excluded, exact p = 1
- linkability by merge week runs from 82.4 to 87.4 percent, and the strict tag share from 1.28 to 1.60 percent of User-authored linkable records

The result is uncertain. The intervals include zero and permit a meaningful positive difference. The data establishes neither equal rates nor a reliable difference.

## Validation

The collectors fail closed on a missing, undersized, invalid gzip, or invalid JSON hour. They write to temporary files and replace the prior CSV only after every requested hour succeeds. Each manifest records all source hours and binds the derived CSV by byte size and SHA-256.

The documented regression suite contains 83 tests, counted from the test files with the `unittest` loader: 9 in `test_detect_ai_commits.py`, 4 in `test_gharchive_collect.py`, 2 in `test_gha_run_year.py`, 16 in `test_analyze_gha.py`, 23 in `test_gha_reverts.py`, 14 in `test_robustness.py`, 4 in `test_make_effect_figure.py`, and 11 in `test_validate_spotcheck.py`. The suite covers strict named-assistant identities, collection boundaries, event-window and containment rules, qualifying review filtering, duplicate events, repository-scoped and branch-scoped revert evidence, merged-only revert pull requests, exact fourteen-day outcomes, interrupted-run preservation, manifest mismatch refusal, statistical helpers, repository-level analysis, validation-fixture contracts, and figure generation.

### Public API field validation

The field check uses a fixed private coordinate fixture of ten records, five tagged and five comparison, all opened by `User` accounts, selected from the final strict CSV for continued public availability and bounded commit checks. It is not an inferential random sample, and it replaces the earlier seeded draw. The set was revised once at 20:23 UTC to require `User` authors, which replaced two of the ten records, and the earlier set is archived as `validation_spotcheck_coordinates.pre-user-filter-2026-11-19.json`. Because availability was a selection criterion, ten of ten reachable records is expected and says nothing about availability in the wider sample.

Results recorded on 2026-11-19 at 21:23 UTC:

- 10 records checked, 0 unavailable, 0 records with mismatches
- immutable fields matched on every record: merge timestamp, author type, commits, changed lines, files changed
- comment and review-comment counts are merge-time snapshots. No current value fell below its snapshot, and one record's current issue-comment count is higher, which is ordinary post-merge discussion
- expected and detected tagged labels agree exactly: Claude 2, Copilot 2, Devin 1
- bound input manifest: 156 of 156 hours, CSV 90,923,695 bytes, SHA-256 `85b2e73d...`
- coordinate file SHA-256: `cfa56f58b420ad704bcd2ffe227a3906914973e9373b126c5a74a9b943d2731e`

The check covers closed-event fields and label reproduction on those ten records. It does not estimate label precision or recall, does not test formal review-event capture, and cannot show that the event stream is complete.

### Privacy of the validation fixture

At 22:36 UTC, the redacted aggregate was regenerated from the unchanged 21:23 detailed result to add the aggregate two-comment increase. No API request was repeated, and the checked fields and label outcomes did not change.

The detailed result `data/validation_spotcheck.json` and the coordinate file `data/validation_spotcheck_coordinates.json` hold the repository and pull-request identifiers of the checked records and link them to the derived tag. Both stay out of version control and are referenced publicly only by the hash above. The released artifact is `data/validation_spotcheck_summary.json`, a redacted aggregate carrying counts, field names, hashes, and match flags with no repository or pull-request identifiers. The detailed fixture can be provided to reviewers on request. Commit messages are not retained.

## Final manuscript format validation

The final private manuscript has an eight-word title, a 142-word abstract under the strict tokenizer below, one table, no figure, and 20 references. The conservative length check counts tokens matching `[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*`, includes headings and references, excludes the table's raw cell text, and adds 300 words for the table. It gives 5,571 words. Whitespace-based and heading-excluded variants are lower. Every measured variant falls between 4,500 and 6,000 words.

The generic Word proof round-trips to the complete title, article, table, funding statement, and all 20 reference titles, with internal workflow fields absent. The PDF proof has nine Letter-size pages. Its title appears once, Table 1 is legible, all 20 reference titles are extractable, and representative first, table, and final pages passed visual inspection.

## Superseded artifacts

Earlier outputs are retained privately by the author for audit and recovery. They are not published and are not part of the final results.

Key superseded dataset commitments:

- The 144-hour review CSV later replaced by the 156-hour rebuild was 90,946,398 bytes with SHA-256 prefix `b70cd2cc`.
- The pre-creation-filter review CSV was 90,946,495 bytes with SHA-256 prefix `052ff59c`.
- The pre-branch-filter revert CSV was 258,567,970 bytes with SHA-256 prefix `f69c33a5`.
- Interrupted collector partials, superseded summaries and figures, earlier manuscript versions, and earlier SciPub drafts remain private.

## Superseded paths

The earlier GitHub API corpus, uncontained review calculations, all-review metrics, uncensored revert outcome, ambiguous revert-number matching, repository-wide short-SHA matching, and varying follow-up analysis are retained only in Git history or private backups. `discover_corpus.py`, `pr_collect.py`, and the local API path remain as method history and are not part of the reproduction flow.

## Sources

- ⚠️ External link, [GH Archive](https://gharchive.org/), accessed 2026-11-19
- ⚠️ External link, [GitHub REST pull-request API](https://docs.github.com/en/rest/pulls/pulls), accessed 2026-11-19
- ⚠️ External link, [GitHub Events API payload changes](https://github.blog/changelog/2025-08-08-upcoming-changes-to-github-events-api-payloads/), accessed 2026-11-18
