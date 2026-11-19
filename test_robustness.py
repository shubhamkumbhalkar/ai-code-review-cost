import csv
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))
import robustness

UTC = dt.timezone.utc
FIELDS = [
    "repo", "pr", "is_ai", "ai_tools", "linkable", "author_type", "reverted",
    "reverted_at",
    "time_to_merge_h", "review_rounds", "commits", "pr_size",
    "files_changed", "comments", "author_assoc", "merged_at",
]


class StatisticalHelperTests(unittest.TestCase):
    def test_exact_sign_test_excludes_ties_before_call(self):
        self.assertEqual(1.0, robustness.exact_binomial_sign_p(1, 1))
        self.assertEqual(0.25, robustness.exact_binomial_sign_p(0, 3))
        self.assertEqual(1.0, robustness.exact_binomial_sign_p(0, 0))

    def test_permutation_is_deterministic(self):
        first = robustness.permutation_result(10, 100, 80, 1000, 500, 9)
        second = robustness.permutation_result(10, 100, 80, 1000, 500, 9)
        self.assertEqual(first, second)

    def test_two_proportion_zero_difference(self):
        values = robustness.two_proportion(1, 100, 2, 200)
        self.assertAlmostEqual(0.0, values[2])
        self.assertAlmostEqual(1.0, values[6])

    def test_wilson_interval_for_zero_successes(self):
        low, high = robustness.wilson_interval(0, 10)
        self.assertAlmostEqual(0.0, low, places=12)
        self.assertAlmostEqual(0.2775, high, places=4)

    def test_newcombe_published_example(self):
        values = robustness.two_proportion(56, 70, 48, 80)
        self.assertAlmostEqual(5.2429, values[3], places=4)
        self.assertAlmostEqual(33.3875, values[4], places=4)

    def test_clustered_standard_error_known_values(self):
        counts = {
            "r1": {0: [10, 1], 1: [10, 2]},
            "r2": {0: [20, 2], 1: [5, 0]},
            "r3": {0: [10, 0], 1: [5, 1]},
        }
        result = robustness.cluster_robust_difference(counts)
        self.assertAlmostEqual(7.5, result["difference_pp"])
        self.assertAlmostEqual(7.577722, result["standard_error_pp"], places=6)


class CohortTests(unittest.TestCase):
    def _write(self, rows):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "reverts.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return directory, path

    @staticmethod
    def _row(repo, pr, ai, linkable, reverted, merged_at, size):
        merged = dt.datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
        return {
            "repo": repo,
            "pr": pr,
            "is_ai": ai,
            "ai_tools": "claude" if ai else "",
            "linkable": linkable,
            "author_type": "User",
            "reverted": reverted,
            "reverted_at": (
                (merged + dt.timedelta(days=1)).isoformat().replace("+00:00", "Z")
                if reverted else ""
            ),
            "time_to_merge_h": 1,
            "review_rounds": "",
            "commits": 1,
            "pr_size": size,
            "files_changed": 1,
            "comments": 0,
            "author_assoc": "OWNER",
            "merged_at": merged_at,
        }

    def test_cutoff_and_linkability_are_applied(self):
        bot = self._row("r2", 6, 0, 1, 1, "2025-08-10T00:00:00Z", 10)
        bot["author_type"] = "Bot"
        unknown = self._row("r3", 7, 0, 1, 1, "2025-08-10T00:00:00Z", 10)
        unknown["author_type"] = ""
        rows = [
            self._row("r1", 1, 1, 1, 1, "2025-08-01T00:00:00Z", 10),
            self._row("r1", 2, 0, 1, 0, "2025-08-16T23:59:59Z", 100),
            self._row("r1", 3, 1, 1, 1, "2025-08-17T00:00:00Z", 10),
            self._row("r1", 4, 1, 0, 1, "2025-08-10T00:00:00Z", 10),
            self._row("r1", 5, 0, 1, 1, "2025-08-31T00:00:00Z", 10),
            bot,
            unknown,
        ]
        directory, path = self._write(rows)
        self.addCleanup(directory.cleanup)
        data = robustness.load_counts(path, dt.datetime(2025, 8, 1, tzinfo=UTC), 30, 14)
        self.assertEqual(7, data["counters"]["rows_total"])
        self.assertEqual(1, data["counters"]["unlinked"])
        self.assertEqual(1, data["counters"]["bot_authored_excluded"])
        self.assertEqual(1, data["counters"]["unknown_author_type_excluded"])
        self.assertEqual(1, data["counters"]["right_censored"])
        self.assertEqual(1, data["counters"]["outside_window"])
        self.assertEqual(2, data["counters"]["cohort"])
        self.assertEqual([1, 1], data["overall"][1])
        self.assertEqual([1, 0], data["overall"][0])

    def test_fixed_followup_boundary(self):
        included = self._row("r1", 1, 1, 1, 1, "2025-08-01T00:00:00Z", 10)
        excluded = self._row("r1", 2, 1, 1, 1, "2025-08-01T00:00:00Z", 10)
        included["reverted_at"] = "2025-08-15T00:00:00Z"
        excluded["reverted_at"] = "2025-08-15T00:00:01Z"
        directory, path = self._write([included, excluded])
        self.addCleanup(directory.cleanup)
        data = robustness.load_counts(path, dt.datetime(2025, 8, 1, tzinfo=UTC), 30, 14)
        self.assertEqual([2, 1], data["overall"][1])
        self.assertEqual(1, data["counters"]["reverts_within_followup"])
        self.assertEqual(1, data["counters"]["reverts_after_followup"])

    def test_within_repository_sign_test_exercises_threshold(self):
        rows = []
        number = 1
        for repo in ("higher", "lower", "tie"):
            for ai in (0, 1):
                for index in range(20):
                    reverted = int(
                        (repo == "higher" and ai == 1 and index == 0)
                        or (repo == "lower" and ai == 0 and index == 0)
                    )
                    rows.append(self._row(
                        repo, number, ai, 1, reverted,
                        f"2025-08-{index % 10 + 1:02d}T00:00:00Z", 10,
                    ))
                    number += 1
        directory, path = self._write(rows)
        self.addCleanup(directory.cleanup)
        result = robustness.analyze(
            path, dt.datetime(2025, 8, 1, tzinfo=UTC), 30, 14, 200, 8
        )
        sign = result["within_repository_sign_test"]
        self.assertEqual(3, sign["eligible_repositories"])
        self.assertEqual(1, sign["higher"])
        self.assertEqual(1, sign["lower"])
        self.assertEqual(1, sign["ties_excluded"])
        self.assertEqual(1.0, sign["two_sided_exact_p"])

    def test_end_to_end_analysis_returns_clustered_and_standardized_results(self):
        rows = []
        number = 1
        for repo in ("r1", "r2", "r3"):
            for ai in (0, 1):
                for index in range(6):
                    rows.append(
                        self._row(
                            repo,
                            number,
                            ai,
                            1,
                            int((repo == "r1" and ai == 1 and index == 0)
                                or (repo == "r2" and ai == 0 and index == 0)),
                            f"2025-08-{index + 1:02d}T00:00:00Z",
                            25 if index < 3 else 400,
                        )
                    )
                    number += 1
        directory, path = self._write(rows)
        self.addCleanup(directory.cleanup)
        result = robustness.analyze(
            path, dt.datetime(2025, 8, 1, tzinfo=UTC), 30, 14, 500, 4
        )
        self.assertEqual(36, result["counters"]["cohort"])
        self.assertEqual(18, result["overall"]["ai_count"])
        self.assertEqual(18, result["overall"]["non_ai_count"])
        self.assertEqual(2, len(result["size_strata"]))
        clustered = result["repository_cluster_robust"]
        self.assertEqual(3, clustered["repositories"])
        self.assertAlmostEqual(0.0, clustered["difference_pp"])
        self.assertGreater(clustered["standard_error_pp"], 0)
        self.assertEqual(1, len(result["weekly_coverage"]))
        rendered = robustness.render(result)
        self.assertIn("standardized to non-AI size mix", rendered)
        self.assertIn("LINKABILITY AND STRICT TAG SHARE BY MERGE WEEK", rendered)
        result["weekly_coverage"][0]["ai_pct_of_human_linkable"] = None
        self.assertIn("strict AI n/a", robustness.render(result))


class ManifestTests(unittest.TestCase):
    def _files(self):
        directory = tempfile.TemporaryDirectory()
        data = Path(directory.name) / "reverts.csv"
        data.write_text("content\n")
        manifest = Path(directory.name) / "reverts.csv.manifest.json"
        payload = {
            "status": "complete",
            "requested_hours": 720,
            "completed_hours": 720,
            "start_utc": "2025-08-01T00:00:00+00:00",
            "window_end_exclusive_utc": "2025-08-31T00:00:00+00:00",
            "csv_bytes": data.stat().st_size,
            "csv_sha256": robustness.file_sha256(data),
        }
        manifest.write_text(json.dumps(payload))
        return directory, data, manifest

    def test_matching_manifest_is_accepted(self):
        directory, data, manifest = self._files()
        self.addCleanup(directory.cleanup)
        result = robustness.validate_manifest(
            data, manifest, dt.datetime(2025, 8, 1, tzinfo=UTC), 30
        )
        self.assertEqual(720, result["completed_hours"])

    def test_incomplete_manifest_is_rejected(self):
        for field, value, message in (
            ("status", "partial", "not complete"),
            ("completed_hours", 719, "incomplete hours"),
        ):
            with self.subTest(field=field):
                directory, data, manifest = self._files()
                self.addCleanup(directory.cleanup)
                payload = json.loads(manifest.read_text())
                payload[field] = value
                manifest.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError, message):
                    robustness.validate_manifest(
                        data, manifest, dt.datetime(2025, 8, 1, tzinfo=UTC), 30
                    )

    def test_mismatched_hash_is_rejected(self):
        directory, data, manifest = self._files()
        self.addCleanup(directory.cleanup)
        data.write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "byte size|SHA-256"):
            robustness.validate_manifest(
                data, manifest, dt.datetime(2025, 8, 1, tzinfo=UTC), 30
            )

    def test_mismatched_window_is_rejected(self):
        directory, data, manifest = self._files()
        self.addCleanup(directory.cleanup)
        with self.assertRaisesRegex(ValueError, "start"):
            robustness.validate_manifest(
                data, manifest, dt.datetime(2025, 8, 2, tzinfo=UTC), 30
            )


if __name__ == "__main__":
    unittest.main()
