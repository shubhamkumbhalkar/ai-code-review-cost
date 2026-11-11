#!/usr/bin/env python3
"""Assemble a study corpus: find active public repos that carry real AI-commit trailers, then
measure each one's AI density with the validated detector, and keep the ones with enough
AI-assisted commits for a within-repo comparison.

Token: reads a GitHub PAT from ~/.github_token if present (read-only, public_repo). The token
raises the search rate limit and is required for the later PR-metric collection. Discovery can
run token-light but is slower and rate-limited without one.

Usage:
    python3 code/discover_corpus.py --min-ai 15 --max-repos 60
Outputs data/corpus.json: [{repo, total_commits, ai_commits, per_tool}], qualifying repos only.
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import detect_ai_commits as det

TOKEN = ""
tp = Path.home() / ".github_token"
if tp.exists():
    TOKEN = tp.read_text().strip()

HDR = {"User-Agent": "ai-code-review-cost/1.0", "Accept": "application/vnd.github+json"}
if TOKEN:
    HDR["Authorization"] = "Bearer " + TOKEN

# Trailer queries that indicate genuine AI-assisted commits (used to find candidate repos).
SEARCH_QUERIES = [
    'Co-authored-by: Claude',
    'Co-authored-by: Cursor',
    'Co-authored-by: Devin',
    'Generated with Claude Code',
]


def _get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=30) as r:
            return json.load(r)
    except Exception as e:
        print(f"  search error: {str(e)[:80]}")
        return {}


def candidate_repos(pages=2):
    """Collect candidate repo full-names from GitHub commit search across the trailer queries."""
    found = {}
    for q in SEARCH_QUERIES:
        for page in range(1, pages + 1):
            u = ("https://api.github.com/search/commits?q=" + urllib.parse.quote(q)
                 + f"&per_page=30&page={page}&sort=committer-date&order=desc")
            d = _get(u)
            items = d.get("items", [])
            for it in items:
                repo = it.get("repository", {})
                name = repo.get("full_name")
                if not name:
                    continue
                found[name] = found.get(name, 0) + 1   # how often it shows up = rough density hint
            if not items:
                break
            time.sleep(2 if TOKEN else 6)              # respect search rate limits
    # most-frequently-seen candidates first
    return [r for r, _ in sorted(found.items(), key=lambda x: -x[1])]


# Option B: popular AI/agent repositories, which are more likely to be substantial AND to tag
# AI commits. We still verify each one's real trailer density before keeping it.
REPO_SEARCH_QUERIES = [
    "topic:ai-agents stars:>500",
    "topic:llm stars:>800",
    "topic:mcp stars:>300",
    "topic:ai-coding stars:>300",
    "coding agent stars:>500",
    "topic:developer-tools ai stars:>800",
]


def repo_search_candidates(per_query=30, pages=2):
    """Candidate repos from GitHub repository search (popular, AI-adjacent)."""
    found = []
    seen = set()
    for q in REPO_SEARCH_QUERIES:
        for page in range(1, pages + 1):
            u = ("https://api.github.com/search/repositories?q=" + urllib.parse.quote(q)
                 + f"&sort=stars&order=desc&per_page={per_query}&page={page}")
            d = _get(u)
            items = d.get("items", [])
            for it in items:
                name = it.get("full_name")
                if name and name not in seen:
                    seen.add(name)
                    found.append(name)
            if not items:
                break
            time.sleep(2 if TOKEN else 6)
    return found


def repo_meta(repo):
    """Stars + last-push date, to bias the corpus toward substantial, active projects."""
    d = _get(f"https://api.github.com/repos/{repo}")
    return d.get("stargazers_count", 0), (d.get("pushed_at", "") or "")[:10]


def measure(repo, workdir):
    """Blobless clone + validated detector. Returns density stats or None on failure."""
    dest = os.path.join(workdir, repo.replace("/", "__"))
    r = subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                        f"https://github.com/{repo}", dest],
                       capture_output=True, text=True, timeout=240)
    if r.returncode != 0:
        return None
    stats = det.analyze(dest)
    subprocess.run(["rm", "-rf", dest])
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-ai", type=int, default=15, help="keep repos with >= this many AI commits")
    ap.add_argument("--max-repos", type=int, default=60, help="stop after this many qualifying repos")
    ap.add_argument("--max-check", type=int, default=150, help="cap candidates measured")
    ap.add_argument("--min-stars", type=int, default=50, help="skip repos below this star count")
    args = ap.parse_args()

    print(f"token: {'yes' if TOKEN else 'NO (rate-limited, slower)'}")
    # Option B first (popular AI repos), then Option A (deeper commit-search), deduped.
    repo_cands = repo_search_candidates(pages=2)
    commit_cands = candidate_repos(pages=6)
    seen = set()
    cands = []
    for name in repo_cands + commit_cands:
        if name not in seen:
            seen.add(name)
            cands.append(name)
    print(f"candidates: {len(repo_cands)} from repo-search + commit-search merged = {len(cands)} unique")

    corpus = []
    out = Path(__file__).resolve().parent.parent / "data" / "corpus.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as wd:
        for i, repo in enumerate(cands[:args.max_check]):
            if len(corpus) >= args.max_repos:
                break
            stars, pushed = repo_meta(repo)
            if stars < args.min_stars:
                print(f"  [{i}] {repo}: {stars}★ -> drop (below {args.min_stars}★)")
                continue
            s = measure(repo, wd)
            if not s:
                print(f"  [{i}] {repo}: clone failed/skip")
                continue
            tag = "KEEP" if s["ai"] >= args.min_ai else "drop"
            print(f"  [{i}] {repo}: {stars}★, {s['ai']} AI / {s['total']} total -> {tag}")
            if s["ai"] >= args.min_ai:
                corpus.append({"repo": repo, "stars": stars, "pushed_at": pushed,
                               "total_commits": s["total"], "ai_commits": s["ai"],
                               "per_tool": s["per_tool"]})
                out.write_text(json.dumps(corpus, indent=2))   # checkpoint each keep
    print(f"\nqualifying repos: {len(corpus)} (>= {args.min_ai} AI commits, >= {args.min_stars} stars). saved -> {out}")


if __name__ == "__main__":
    main()
