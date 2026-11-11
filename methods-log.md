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
