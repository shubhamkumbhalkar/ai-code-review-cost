import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))
import validate_spotcheck


class SpotCheckTests(unittest.TestCase):
    def _write_input(self, directory, rows):
        data = Path(directory) / "review.csv"
        pd.DataFrame(rows).to_csv(data, index=False)
        manifest = Path(str(data) + ".manifest.json")
        manifest.write_text(json.dumps({
            "status": "complete",
            "requested_hours": 2,
            "completed_hours": 2,
            "csv_bytes": data.stat().st_size,
            "csv_sha256": validate_spotcheck.file_sha256(data),
        }))
        return data

    def test_matching_values_have_no_differences(self):
        row = SimpleNamespace(
            merged_at="2025-01-01T00:00:00Z",
            commits=2,
            pr_size=15,
            files_changed=3,
            comments=1,
            review_comments=4,
            author_type="User",
        )
        payload = {
            "merged_at": "2025-01-01T00:00:00Z",
            "commits": 2,
            "additions": 10,
            "deletions": 5,
            "changed_files": 3,
            "comments": 1,
            "review_comments": 4,
            "user": {"type": "User"},
        }
        self.assertEqual(
            {},
            validate_spotcheck.compare(
                validate_spotcheck.extracted_values(row),
                validate_spotcheck.api_values(payload),
            ),
        )

    def test_mismatch_names_only_decreased_comment_field(self):
        expected = {name: 1 for name in validate_spotcheck.FIELDS}
        actual = dict(expected)
        actual["comments"] = 0
        self.assertEqual(
            {"comments": {"expected_minimum": 1, "actual": 0}},
            validate_spotcheck.compare(expected, actual),
        )

    def test_post_merge_comment_increase_is_reported_not_rejected(self):
        expected = {name: 1 for name in validate_spotcheck.FIELDS}
        actual = dict(expected)
        actual["comments"] = 3
        self.assertEqual({}, validate_spotcheck.compare(expected, actual))
        self.assertEqual(
            {"comments": {"merge_time": 1, "current": 3, "increase": 2}},
            validate_spotcheck.post_merge_increases(expected, actual),
        )
    def test_run_rejects_bot_coordinate_after_population_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            data = self._write_input(directory, [{
                "repo": "bot/repo", "pr": 1, "is_ai": 1, "ai_tools": "claude",
                "linkable": 1, "author_type": "Bot", "merged_at": "2025-01-01T00:00:00Z",
                "commits": 1, "pr_size": 1, "files_changed": 1,
                "comments": 0, "review_comments": 0,
            }])
            coordinates = Path(directory) / "coordinates.json"
            coordinates.write_text(json.dumps([
                {"repo": "bot/repo", "pr": 1, "is_ai": 1}
            ]))
            with self.assertRaisesRegex(ValueError, "match one final row"):
                validate_spotcheck.run(
                    data, Path(directory) / "detail.json", 1, 1, 1,
                    coordinates, Path(directory) / "summary.json",
                )

    def test_run_validates_tag_from_merge_commit_fallback(self):
        rows = [
            {
                "repo": "a/repo", "pr": 1, "is_ai": 1, "ai_tools": "claude",
                "linkable": 1, "author_type": "User", "merged_at": "2025-01-01T00:00:00Z",
                "commits": 1, "pr_size": 3, "files_changed": 1,
                "comments": 0, "review_comments": 0,
            },
            {
                "repo": "b/repo", "pr": 2, "is_ai": 0, "ai_tools": "",
                "linkable": 1, "author_type": "User", "merged_at": "2025-01-02T00:00:00Z",
                "commits": 1, "pr_size": 5, "files_changed": 1,
                "comments": 0, "review_comments": 0,
            },
        ]

        class Response:
            def __init__(self, payload):
                self.status_code = 200
                self.headers = {"X-RateLimit-Remaining": "10"}
                self.text = ""
                self._payload = payload

            def json(self):
                return self._payload

        def pull_payload(merged_at, size, merge_sha):
            return {
                "merged_at": merged_at,
                "commits": 1,
                "additions": size,
                "deletions": 0,
                "changed_files": 1,
                "comments": 0,
                "review_comments": 0,
                "user": {"type": "User"},
                "merge_commit_sha": merge_sha,
            }

        merge_sha = "a" * 40
        responses = {
            "https://api.github.com/repos/a/repo/pulls/1": Response(
                pull_payload("2025-01-01T00:00:00Z", 3, merge_sha)
            ),
            "https://api.github.com/repos/a/repo/pulls/1/commits": Response([
                {"commit": {"message": "ordinary commit"}}
            ]),
            f"https://api.github.com/repos/a/repo/commits/{merge_sha}": Response({
                "commit": {
                    "message": "Co-authored-by: Claude <noreply@anthropic.com>"
                }
            }),
            "https://api.github.com/repos/b/repo/pulls/2": Response(
                pull_payload("2025-01-02T00:00:00Z", 5, "b" * 40)
            ),
        }

        class Session:
            def __init__(self):
                self.headers = {}

            def get(self, url, params=None, timeout=None):
                return responses[url]

        with tempfile.TemporaryDirectory() as directory:
            data = self._write_input(directory, rows)
            coordinates = Path(directory) / "coordinates.json"
            coordinates.write_text(json.dumps([
                {"repo": "a/repo", "pr": 1, "is_ai": 1},
                {"repo": "b/repo", "pr": 2, "is_ai": 0},
            ]))
            detail = Path(directory) / "detail.json"
            summary = Path(directory) / "summary.json"
            with patch.object(validate_spotcheck.requests, "Session", Session), patch.object(
                validate_spotcheck.time, "sleep", return_value=None
            ):
                result = validate_spotcheck.run(
                    data, detail, 1, 2, 1, coordinates, summary
                )
            self.assertTrue(result["all_checked_fields_match"])
            self.assertTrue(result["all_tagged_labels_match"])
            label = result["checked"][0]["label_validation"]
            self.assertEqual(["merge_commit"], label["evidence_sources"])
            self.assertEqual(["claude"], label["detected_tools"])
            self.assertNotIn("a/repo", summary.read_text())

    def test_aggregate_result_omits_row_identifiers(self):
        detailed = {
            "accessed_utc": "2026-11-19T00:00:00+00:00",
            "target": 1,
            "input_manifest": {"csv_sha256": "abc"},
            "selection": {"mode": "fixed_coordinates", "coordinates_sha256": "def"},
            "target_by_group": {"ai": 1, "non_ai": 0},
            "unavailable": [],
            "all_checked_fields_match": True,
            "all_tagged_labels_match": True,
            "checked": [{
                "repo": "person/project",
                "pr": 7,
                "mismatches": {},
                "post_merge_increases": {
                    "comments": {"merge_time": 1, "current": 2, "increase": 1}
                },
                "label_validation": {
                    "expected_tools": ["claude"],
                    "detected_tools": ["claude"],
                    "match": True,
                },
            }],
        }
        aggregate = validate_spotcheck.aggregate_result(detailed)
        serialized = json.dumps(aggregate)
        self.assertNotIn("person/project", serialized)
        self.assertNotIn('"pr"', serialized)
        self.assertEqual(1, aggregate["checked_count"])
        self.assertEqual({"comments": 1}, aggregate["post_merge_increases_by_field"])
        self.assertEqual(
            {"comments": 1}, aggregate["post_merge_increase_amounts_by_field"]
        )

    def test_api_commit_tools_uses_strict_detector(self):
        commits = [
            {"commit": {"message": "Fix\n\nCo-authored-by: Claude <noreply@anthropic.com>"}},
            {"commit": {"message": "Co-authored-by: Devin Smith <devin@example.com>"}},
            {"commit": {"message": "Co-authored-by: Cursor Agent <cursor@example.com>"}},
        ]
        self.assertEqual(
            ["claude", "cursor"], validate_spotcheck.api_commit_tools(commits)
        )

    def test_rate_limit_detection_distinguishes_forbidden_response(self):
        limited = SimpleNamespace(
            status_code=403,
            headers={"X-RateLimit-Remaining": "0"},
            json=lambda: {"message": "API rate limit exceeded"},
            text="",
        )
        forbidden = SimpleNamespace(
            status_code=403,
            headers={"X-RateLimit-Remaining": "34"},
            json=lambda: {"message": "Resource not accessible"},
            text="",
        )
        throttled = SimpleNamespace(
            status_code=429, headers={}, json=lambda: {}, text=""
        )
        self.assertTrue(validate_spotcheck.is_rate_limited(limited))
        self.assertFalse(validate_spotcheck.is_rate_limited(forbidden))
        self.assertTrue(validate_spotcheck.is_rate_limited(throttled))

    def test_input_manifest_binds_csv_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "review.csv"
            data.write_text("value\n1\n")
            manifest = Path(str(data) + ".manifest.json")
            manifest.write_text(json.dumps({
                "status": "complete",
                "requested_hours": 2,
                "completed_hours": 2,
                "csv_bytes": data.stat().st_size,
                "csv_sha256": validate_spotcheck.file_sha256(data),
            }))
            result = validate_spotcheck.validate_input_manifest(data)
            self.assertEqual(validate_spotcheck.file_sha256(data), result["csv_sha256"])
            data.write_text("value\n2\n")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                validate_spotcheck.validate_input_manifest(data)

    def test_fixed_coordinates_require_final_labels_and_balance(self):
        frame = pd.DataFrame([
            {"repo": "a/repo", "pr": 1, "is_ai": 1},
            {"repo": "b/repo", "pr": 2, "is_ai": 0},
        ])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "coordinates.json"
            source.write_text(json.dumps([
                {"repo": "a/repo", "pr": 1, "is_ai": 1},
                {"repo": "b/repo", "pr": 2, "is_ai": 0},
            ]))
            samples, selection = validate_spotcheck.fixed_coordinate_samples(
                frame, source, {1: 1, 0: 1}
            )
            self.assertEqual([1, 0], [group for group, _, _ in samples])
            self.assertEqual("fixed_coordinates", selection["mode"])
            self.assertEqual(
                validate_spotcheck.file_sha256(source),
                selection["coordinates_sha256"],
            )

    def test_fixed_coordinates_reject_stale_label(self):
        frame = pd.DataFrame([{"repo": "a/repo", "pr": 1, "is_ai": 0}])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "coordinates.json"
            source.write_text(json.dumps([
                {"repo": "a/repo", "pr": 1, "is_ai": 1},
            ]))
            with self.assertRaisesRegex(ValueError, "label mismatch"):
                validate_spotcheck.fixed_coordinate_samples(
                    frame, source, {1: 1, 0: 0}
                )


if __name__ == "__main__":
    unittest.main()
