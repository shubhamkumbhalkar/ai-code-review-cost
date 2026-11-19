import csv
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))
import gha_run_year


class ReviewDriverTests(unittest.TestCase):
    @staticmethod
    def _collector_result(hours, analysis_hour_keys):
        assert len(hours) == 13
        assert len(analysis_hour_keys) == 12
        first = dt.datetime.strptime(hours[0], "%Y-%m-%d-%H")
        created = first.replace(minute=10).strftime("%Y-%m-%dT%H:%M:%SZ")
        merged = first.replace(minute=40).strftime("%Y-%m-%dT%H:%M:%SZ")
        context_start = first + dt.timedelta(hours=12)
        late_merge = (context_start - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pull_request = {
            "repo": "owner/project",
            "pr": 1,
            "created_at": created,
            "merged_at": merged,
            "commits": 1,
            "pr_size": 10,
            "files_changed": 1,
            "comments": 0,
            "review_comments": 0,
            "author_assoc": "OWNER",
            "author_login": "author",
            "author_type": "User",
            "head_sha": "a" * 40,
            "merge_sha": "b" * 40,
        }
        late_pull = dict(pull_request, pr=2, created_at=created, merged_at=late_merge,
                         head_sha="c" * 40, merge_sha="d" * 40)
        records = [{"hour": hour, "bytes": 2_000_000, "events": 10} for hour in hours]
        observed = first.replace(minute=20, tzinfo=dt.timezone.utc)
        context_observed = context_start.replace(minute=10, tzinfo=dt.timezone.utc)
        return (
            {
                ("owner/project", 1): pull_request,
                ("owner/project", 2): late_pull,
            },
            {},
            {
                "a" * 40: {"observed_at": observed, "tools": {"claude"}},
                "d" * 40: {"observed_at": context_observed, "tools": {"claude"}},
            },
            {
                "a" * 40: observed,
                "d" * 40: context_observed,
            },
            records,
        )

    def test_build_writes_all_months_and_complete_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review.csv"
            with patch.object(
                gha_run_year.collector,
                "collect",
                side_effect=lambda hours, analysis_hour_keys=None: self._collector_result(
                    hours, analysis_hour_keys
                ),
            ) as mocked:
                manifest = gha_run_year.build(output)
            self.assertEqual(12, mocked.call_count)
            self.assertEqual(156, manifest["completed_hours"])
            self.assertEqual(144, manifest["analysis_hours"])
            self.assertEqual(12, manifest["context_hours"])
            self.assertEqual(24, manifest["totals"]["rows"])
            self.assertEqual(24, manifest["totals"]["linkable"])
            self.assertEqual(24, manifest["totals"]["ai"])
            with output.open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(24, len(rows))
            self.assertFalse(any(int(row["pr"]) == 3 for row in rows))
            self.assertTrue(all(int(row["linkable"]) == 1 for row in rows))
            saved = json.loads(output.with_suffix(".csv.manifest.json").read_text())
            self.assertEqual("complete", saved["status"])
            self.assertEqual(output.stat().st_size, saved["csv_bytes"])
            self.assertEqual(gha_run_year.file_sha256(output), saved["csv_sha256"])

    def test_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review.csv"
            output.write_text("validated old data\n")
            with patch.object(
                gha_run_year.collector,
                "collect",
                side_effect=RuntimeError("simulated missing hour"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated missing hour"):
                    gha_run_year.build(output)
            self.assertEqual("validated old data\n", output.read_text())
            self.assertFalse(output.with_suffix(".csv.manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
