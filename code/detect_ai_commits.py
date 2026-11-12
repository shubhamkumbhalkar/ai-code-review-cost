#!/usr/bin/env python3
"""Detect AI-assisted commits from EXPLICIT, machine-written trailers only.

Design decision (validated on a pilot): accept a commit as AI-assisted only when it carries a
recognized co-author trailer or an explicit tool-generation line. Do NOT match the bare vendor
word anywhere in the message. A pilot showed the loose form matched normal commits like
"auto-scroll to cursor position", which would contaminate the AI group. Strict trailers only.

Usage:
    python3 detect_ai_commits.py /path/to/cloned/repo
    # prints counts per signal and the overall AI-tagged rate, with sample trailers to verify.
"""
import re
import subprocess
import sys
from collections import Counter

# Each pattern is anchored to a trailer/footer form, not the bare vendor word.
AI_PATTERNS = {
    "claude": re.compile(r"co-authored-by:\s*claude\b|generated with\s*\[?claude code", re.I),
    "copilot": re.compile(r"co-authored-by:\s*(github )?copilot\b", re.I),
    "cursor": re.compile(r"co-authored-by:\s*cursor(agent)?\b", re.I),
    "devin": re.compile(r"co-authored-by:\s*devin\b", re.I),
    "aider": re.compile(r"co-authored-by:\s*aider\b", re.I),
    "codex": re.compile(r"co-authored-by:\s*(openai )?codex\b", re.I),
    "generic_agent": re.compile(r"co-authored-by:[^\n]*\b(bot|agent)@|🤖 generated", re.I),
}


def commits(repo):
    """Yield (sha, full_message) for every commit in the repo's history."""
    out = subprocess.run(
        ["git", "-C", repo, "log", "--pretty=%H%x01%B%x02"],
        capture_output=True, text=True).stdout
    for chunk in out.split("\x02"):
        chunk = chunk.strip()
        if not chunk:
            continue
        sha, _, body = chunk.partition("\x01")
        yield sha.strip(), body


def classify(message):
    """Return the set of AI tools detected in one commit message (empty = not AI-assisted)."""
    if not message:
        return set()
    return {name for name, rx in AI_PATTERNS.items() if rx.search(message)}


def analyze(repo):
    total = 0
    ai = 0
    per_tool = Counter()
    samples = []
    for _, body in commits(repo):
        total += 1
        tools = classify(body)
        if tools:
            ai += 1
            for t in tools:
                per_tool[t] += 1
            if len(samples) < 5:
                line = next((l.strip() for l in body.splitlines()
                             if any(p.search(l) for p in AI_PATTERNS.values())), "")
                samples.append(line[:100])
    return {"total": total, "ai": ai, "per_tool": dict(per_tool), "samples": samples}


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    r = analyze(repo)
    print(f"total commits: {r['total']}")
    print(f"AI-assisted:   {r['ai']}  ({100 * r['ai'] / max(r['total'], 1):.2f}%)")
    for tool, n in sorted(r["per_tool"].items(), key=lambda x: -x[1]):
        print(f"  {tool}: {n}")
    print("sample trailers (verify these are real):")
    for s in r["samples"]:
        print("   ", s)
