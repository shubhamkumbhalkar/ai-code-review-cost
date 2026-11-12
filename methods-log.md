# Methods log

## AI-signal definition (2026-11-11)
AI-assisted = a commit carrying an explicit machine-written trailer: `Co-Authored-By: <tool>`
(Claude, Copilot, Cursor/cursoragent, Devin, Aider, Codex, bot/agent co-authors) or an explicit
"Generated with Claude Code" / "🤖 Generated" line. Bare vendor words are NOT matched.

### Why strict (pilot finding)
A loose `cursor` match flagged normal commits ("auto-scroll to cursor position",
"LAUNCH_EDITOR=cursor"). Those are false positives that would contaminate the AI group, so the
detector requires the trailer form only. Precision over recall; the under-count is stated as a
limitation.

## Pilot density (reference)
One random mid-size repo: ~0.8% of commits AI-tagged. Implication: assemble a corpus of many
repos and treat repo as a covariate, rather than expecting an even split per repo.

## Corpus scope + selection bias (2026-11-11)
Discovery finding: AI commit trailers are widespread but concentrated in small/personal repos;
a date-sorted commit search floods with hobby projects (found 1 substantial repo,
assistant-ui at 13k stars / 447 AI commits, among ~37 candidates). This is a real selection
effect, not a tooling bug.

Decisions:
- Candidate discovery uses TWO sources: popular AI-adjacent repository search (substantial,
  recognizable projects) and deeper commit search (breadth). Both are then verified for real
  trailer density before inclusion.
- A minimum star gate biases toward active, credible projects.
- SCOPE is stated honestly: the study population is "public repos that openly tag AI-assisted
  commits." That is a specific, defensible population, not "all software." The paper states this
  up front and lists the selection bias (tagging is voluntary; untagged AI use is invisible;
  taggers may differ systematically from non-taggers) in Threats to Validity.

## GH Archive pivot (2026-11-12)
The study's data source changed from the live GitHub API to GH Archive (gharchive.org).

Reason: while assembling a corpus from the GitHub API in this environment, aggregate metrics came back
implausible (for example, obscure repositories reporting more stars than the Linux kernel, and inflated
counts on well-known repositories). We could not treat that as real public data, and our integrity rule
is to use only real public data. We validated GH Archive against immutable ground truth (the Linux
kernel's 2005 root commit resolves correctly) and confirmed recent months return plausible, real
events, then rebuilt the study on it.

Canonical scripts for the current method:
- code/detect_ai_commits.py - strict AI-trailer detector (shared).
- code/gharchive_collect.py - parse one window of GH Archive hours into a PR-level table (join merged
  PRs + reviews + AI-commit SHAs). Process-and-discard, memory-flat.
- code/gha_run_year.py - 12 monthly windows (Oct 2024 - Sep 2025) for the review-cost dataset.
- code/gha_reverts.py - one contiguous 30-day window for downstream revert measurement.
- code/analyze_gha.py - review-cost analysis (medians, Mann-Whitney, Cliff's delta + bootstrap CI,
  BH correction, within-repo, size-stratified, month-matched).
- code/robustness.py - revert robustness (two-proportion test, size/author control, permutation).

Superseded and kept for history only: discover_corpus.py (and the local-only API collectors). These
reflect the abandoned API path and are not part of the current reproduction.

Scope note: the AI label captures only openly tagged assistant use on public repositories. Reverts are
a floor (they undercount silent rollbacks). Same-file 30-day rework was not estimated because GH Archive
push events carry no file lists.
