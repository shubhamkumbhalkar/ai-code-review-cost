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
