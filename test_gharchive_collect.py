import datetime as dt
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))
import gharchive_collect


class ReviewRowTests(unittest.TestCase):
    def test_primary_review_metrics_use_human_non_author_pre_merge_reviews(self):
        key = ("owner/project", 1)
        pull_requests = {
            key: {
                "repo": "owner/project",
                "pr": 1,
                "created_at": "2025-08-15T06:00:00Z",
                "merged_at": "2025-08-15T07:00:00Z",
                "commits": 2,
                "pr_size": 20,
                "files_changed": 2,
                "comments": 3,
                "review_comments": 4,
                "author_assoc": "OWNER",
                "author_login": "author",
                "author_type": "User",
                "head_sha": "a" * 40,
                "merge_sha": "b" * 40,
            }
        }
        reviews = {
            key: {
                0: {
                    "submitted_at": "2025-08-15T05:50:00Z",
                    "reviewer_login": "early-human",
                    "reviewer_type": "User",
                },
                1: {
                    "submitted_at": "2025-08-15T06:10:00Z",
                    "reviewer_login": "author",
                    "reviewer_type": "User",
                },
                2: {
                    "submitted_at": "2025-08-15T06:20:00Z",
                    "reviewer_login": "review-bot",
                    "reviewer_type": "Bot",
                },
                3: {
                    "submitted_at": "2025-08-15T06:30:00Z",
                    "reviewer_login": "human",
                    "reviewer_type": "User",
                },
                4: {
                    "submitted_at": "2025-08-15T07:10:00Z",
                    "reviewer_login": "late-human",
                    "reviewer_type": "User",
                },
                5: {
                    "submitted_at": "2025-08-15T06:25:00Z",
                    "reviewer_login": None,
                    "reviewer_type": "User",
                },
            }
        }
        observed = dt.datetime.fromisoformat("2025-08-15T06:20:00+00:00")
        after_merge = dt.datetime.fromisoformat("2025-08-15T07:10:00+00:00")
        rows = gharchive_collect.build_rows(
            pull_requests,
            reviews,
            {
                "a" * 40: {"observed_at": observed, "tools": {"claude", "cursor"}},
                "b" * 40: {"observed_at": after_merge, "tools": {"copilot"}},
            },
            {"a" * 40: observed, "b" * 40: after_merge},
        )
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(1, row["is_ai"])
        self.assertEqual("claude,copilot,cursor", row["ai_tools"])
        self.assertEqual("User", row["author_type"])
        self.assertEqual(0.5, row["review_latency_h"])
        self.assertEqual(1, row["review_rounds"])
        self.assertEqual(round(10 / 60, 3), row["all_review_latency_h"])
        self.assertEqual(4, row["all_review_rounds"])

        too_late = dt.datetime.fromisoformat("2025-08-15T08:00:01+00:00")
        late_rows = gharchive_collect.build_rows(
            pull_requests,
            reviews,
            {"b" * 40: {"observed_at": too_late, "tools": {"copilot"}}},
            {"b" * 40: too_late},
        )
        self.assertEqual(0, late_rows[0]["is_ai"])
        self.assertEqual(0, late_rows[0]["linkable"])

    def test_review_dictionary_deduplicates_ids(self):
        reviews = {}
        key = ("owner/project", 1)
        reviews.setdefault(key, {})[123] = {"submitted_at": "a"}
        reviews.setdefault(key, {})[123] = {"submitted_at": "b"}
        self.assertEqual(1, len(reviews[key]))
        self.assertEqual("b", reviews[key][123]["submitted_at"])


class CollectorParsingTests(unittest.TestCase):
    def test_collect_parses_payload_head_and_deduplicates_review_ids(self):
        sha = "a" * 40
        pull_request = {
            "number": 1,
            "merged": True,
            "created_at": "2025-08-15T06:00:00Z",
            "merged_at": "2025-08-15T06:40:00Z",
            "commits": 1,
            "additions": 8,
            "deletions": 2,
            "changed_files": 1,
            "comments": 0,
            "review_comments": 1,
            "author_association": "OWNER",
            "user": {"login": "author", "type": "User"},
            "head": {"sha": sha},
            "merge_commit_sha": "b" * 40,
        }
        review = {
            "id": 99,
            "submitted_at": "2025-08-15T06:20:00Z",
            "state": "approved",
            "user": {"login": "reviewer", "type": "User"},
        }
        events = [
            {
                "type": "PullRequestEvent",
                "created_at": "2025-08-15T06:40:01Z",
                "repo": {"name": "owner/project"},
                "payload": {"action": "closed", "pull_request": pull_request},
            },
            {
                "type": "PullRequestReviewEvent",
                "created_at": "2025-08-15T06:20:00Z",
                "repo": {"name": "owner/project"},
                "payload": {"pull_request": pull_request, "review": review},
            },
            {
                "type": "PullRequestReviewEvent",
                "created_at": "2025-08-15T06:20:01Z",
                "repo": {"name": "owner/project"},
                "payload": {"pull_request": pull_request, "review": review},
            },
            {
                "type": "PushEvent",
                "created_at": "2025-08-15T06:10:00Z",
                "repo": {"name": "owner/project"},
                "payload": {"head": sha, "commits": []},
            },
        ]

        def fake_curl(command, **_kwargs):
            output = Path(command[command.index("-o") + 1])
            with gzip.open(output, "wt") as archive:
                for event in events:
                    archive.write(json.dumps(event) + "\n")
            return SimpleNamespace(returncode=0, stderr="")

        with patch.object(gharchive_collect.subprocess, "run", side_effect=fake_curl), \
             patch.object(gharchive_collect.os.path, "getsize", return_value=2_000_000):
            prs, reviews, ai_shas, all_push, records = gharchive_collect.collect(
                ["2025-08-15-6"]
            )
        self.assertEqual(1, len(prs))
        self.assertEqual(1, len(reviews[("owner/project", 1)]))
        self.assertIn(sha, all_push)
        self.assertEqual({}, ai_shas)
        self.assertEqual(4, records[0]["events"])
        row = gharchive_collect.build_rows(prs, reviews, ai_shas, all_push)[0]
        self.assertEqual(1, row["linkable"])
        self.assertEqual(1, row["review_rounds"])

    def test_context_hour_is_push_only(self):
        sha = "a" * 40
        events = [
            {
                "type": "PullRequestEvent",
                "created_at": "2025-08-15T18:10:00Z",
                "repo": {"name": "owner/project"},
                "payload": {
                    "action": "closed",
                    "pull_request": {"number": 1, "merged": True},
                },
            },
            {
                "type": "PushEvent",
                "created_at": "2025-08-15T18:10:00Z",
                "repo": {"name": "owner/project"},
                "payload": {"head": sha, "commits": []},
            },
        ]

        def fake_curl(command, **_kwargs):
            output = Path(command[command.index("-o") + 1])
            with gzip.open(output, "wt") as archive:
                for event in events:
                    archive.write(json.dumps(event) + "\n")
            return SimpleNamespace(returncode=0, stderr="")

        with patch.object(gharchive_collect.subprocess, "run", side_effect=fake_curl), \
             patch.object(gharchive_collect.os.path, "getsize", return_value=2_000_000):
            prs, reviews, ai_shas, all_push, _records = gharchive_collect.collect(
                ["2025-08-15-18"], analysis_hour_keys=set()
            )
        self.assertEqual({}, prs)
        self.assertEqual({}, reviews)
        self.assertEqual({}, ai_shas)
        self.assertIn(sha, all_push)


if __name__ == "__main__":
    unittest.main()
