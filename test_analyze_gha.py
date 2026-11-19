import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))
import analyze
import analyze_gha


class ContainmentTests(unittest.TestCase):
    def test_block_boundaries_are_start_inclusive_and_end_exclusive(self):
        rows = pd.DataFrame(
            [
                self._row("2025-08-15T06:00:00Z", "2025-08-15T17:59:59Z"),
                self._row("2025-08-15T05:59:59Z", "2025-08-15T10:00:00Z"),
                self._row("2025-08-15T06:00:00Z", "2025-08-15T18:00:00Z"),
            ]
        )
        contained = analyze_gha.contained_review_rows(rows)
        self.assertEqual(1, len(contained))
        self.assertEqual("keep", contained.iloc[0]["repo"])

    @staticmethod
    def _row(created, merged):
        return {
            "repo": "keep" if created.endswith("06:00:00Z") and merged.endswith("17:59:59Z") else "drop",
            "month": "2025-08",
            "created_at": created,
            "merged_at": merged,
        }


class RepositorySensitivityTests(unittest.TestCase):
    def test_mann_whitney_small_p_value_does_not_underflow(self):
        _, p_value = analyze.mann_whitney([0] * 100, [1] * 100)
        self.assertGreater(p_value, 0.0)
        self.assertLess(p_value, 1e-20)

    def test_benjamini_hochberg_known_values(self):
        adjusted = analyze.bh([0.01, 0.04, 0.03])
        self.assertEqual([0.03, 0.04, 0.04], adjusted.tolist())

    def test_exact_sign_test(self):
        self.assertEqual(1.0, analyze_gha.exact_sign_p(1, 1))
        self.assertEqual(0.25, analyze_gha.exact_sign_p(0, 3))
        self.assertEqual(1.0, analyze_gha.exact_sign_p(0, 0))

    def test_repository_differences_require_both_groups(self):
        rows = pd.DataFrame(
            [
                {"repo": "paired", "is_ai": 0, "metric": 1.0},
                {"repo": "paired", "is_ai": 1, "metric": 3.0},
                {"repo": "only-ai", "is_ai": 1, "metric": 9.0},
            ]
        )
        differences = analyze_gha.within_repo_diffs(rows, "metric", 1)
        self.assertEqual([2.0], differences.tolist())

    def test_paired_bootstrap_is_deterministic(self):
        rows = []
        for repo, difference in (("a", -1), ("b", 0), ("c", 2), ("d", 4)):
            rows.extend(
                [
                    {"repo": repo, "is_ai": 0, "metric": 10.0},
                    {"repo": repo, "is_ai": 1, "metric": 10.0 + difference},
                ]
            )
        frame = pd.DataFrame(rows)
        first = analyze_gha.paired_repository_sensitivity(frame, "metric", 500, 7)
        second = analyze_gha.paired_repository_sensitivity(frame, "metric", 500, 7)
        self.assertEqual(first, second)
        self.assertEqual(4, first["repositories"])
        self.assertEqual(2, first["higher"])
        self.assertEqual(1, first["lower"])
        self.assertEqual(1, first["ties_excluded"])

    def test_tool_sensitivity_reports_overlapping_labels(self):
        rows = pd.DataFrame([
            {"is_ai": 0, "ai_tools": "", "time_to_merge_h": 1, "pr_size": 10, "review_rounds": 0},
            {"is_ai": 0, "ai_tools": "", "time_to_merge_h": 2, "pr_size": 20, "review_rounds": 1},
            {"is_ai": 1, "ai_tools": "claude,cursor", "time_to_merge_h": 3, "pr_size": 30, "review_rounds": 2},
            {"is_ai": 1, "ai_tools": "claude", "time_to_merge_h": 4, "pr_size": 40, "review_rounds": 3},
        ])
        result = analyze_gha.tool_sensitivity(rows, rows, minimum=1)
        self.assertEqual(2, result["claude"]["tagged_pull_requests"])
        self.assertEqual(1, result["cursor"]["tagged_pull_requests"])
        self.assertEqual(3.5, result["claude"]["metrics"]["time_to_merge_h"]["median_ai"])


class ReviewIncidenceTests(unittest.TestCase):
    def test_review_counts_by_multiple_strata(self):
        rows = pd.DataFrame([
            {"repo": "a", "band": "small", "is_ai": 1, "review_latency_h": 0.2},
            {"repo": "a", "band": "small", "is_ai": 1, "review_latency_h": None},
            {"repo": "a", "band": "small", "is_ai": 0, "review_latency_h": 0.1},
            {"repo": "b", "band": "large", "is_ai": 0, "review_latency_h": None},
        ])
        counts = analyze_gha._review_counts_by_strata(rows, ["repo", "band"])
        self.assertEqual([2, 1], counts[("a", "small")][1])
        self.assertEqual([1, 1], counts[("a", "small")][0])
        self.assertEqual([1, 0], counts[("b", "large")][0])

    def test_mantel_haenszel_known_two_repository_example(self):
        counts = {
            "a": {1: [10, 2], 0: [10, 4]},
            "b": {1: [20, 6], 0: [20, 10]},
        }
        result = analyze_gha.mantel_haenszel_risk_difference(counts)
        self.assertEqual(2, result["repositories"])
        self.assertEqual(30, result["tagged_count"])
        self.assertEqual(8, result["tagged_reviewed"])
        self.assertAlmostEqual(-20.0, result["difference_pp"])
        self.assertAlmostEqual(-1.588480095, result["cmh_z"], places=6)
        self.assertAlmostEqual(12.1106014, result["sato_standard_error_pp"], places=6)

    def test_mantel_haenszel_unbalanced_single_stratum_matches_wald(self):
        counts = {"a": {1: [10, 2], 0: [20, 8]}}
        result = analyze_gha.mantel_haenszel_risk_difference(counts)
        expected_se_pp = 100 * ((0.2 * 0.8 / 10 + 0.4 * 0.6 / 20) ** 0.5)
        self.assertAlmostEqual(-20.0, result["difference_pp"])
        self.assertAlmostEqual(expected_se_pp, result["sato_standard_error_pp"])

    def test_mantel_haenszel_requires_paired_repositories(self):
        counts = {"only-tagged": {1: [3, 1], 0: [0, 0]}}
        self.assertIsNone(analyze_gha.mantel_haenszel_risk_difference(counts))

    def test_standardization_one_stratum_matches_crude_difference(self):
        rows = pd.DataFrame([
            {"repo": "a", "is_ai": 1, "review_latency_h": 0.2, "month": "2025-01"},
            {"repo": "a", "is_ai": 1, "review_latency_h": None, "month": "2025-01"},
            {"repo": "a", "is_ai": 0, "review_latency_h": 0.1, "month": "2025-01"},
            {"repo": "a", "is_ai": 0, "review_latency_h": None, "month": "2025-01"},
            {"repo": "b", "is_ai": 1, "review_latency_h": None, "month": "2025-01"},
            {"repo": "b", "is_ai": 1, "review_latency_h": None, "month": "2025-01"},
            {"repo": "b", "is_ai": 0, "review_latency_h": 0.3, "month": "2025-01"},
            {"repo": "b", "is_ai": 0, "review_latency_h": None, "month": "2025-01"},
        ])
        result = analyze_gha.standardized_review_incidence(rows, ["month"])
        self.assertAlmostEqual(-25.0, result["difference_pp"])
        self.assertEqual(4, result["tagged_retained"])
        self.assertEqual(0, result["dropped_tagged"])
        self.assertEqual(1, len(result["strata"]))
        clustered = analyze_gha.cluster_robust_difference(
            analyze_gha._review_counts_by_repository(rows)
        )
        self.assertAlmostEqual(
            clustered["standard_error_pp"],
            result["repository_clustered_standard_error_pp"],
        )
    def test_standardization_uses_tagged_distribution(self):
        rows = pd.DataFrame([
            {"repo": "a", "is_ai": 1, "review_latency_h": 0.1, "band": "a"},
            {"repo": "a", "is_ai": 0, "review_latency_h": 0.2, "band": "a"},
            {"repo": "b", "is_ai": 1, "review_latency_h": None, "band": "b"},
            {"repo": "c", "is_ai": 1, "review_latency_h": None, "band": "b"},
            {"repo": "d", "is_ai": 1, "review_latency_h": None, "band": "b"},
            {"repo": "b", "is_ai": 0, "review_latency_h": 0.3, "band": "b"},
        ])
        result = analyze_gha.standardized_review_incidence(rows, ["band"])
        self.assertAlmostEqual(-75.0, result["difference_pp"])
        weights = {row["values"][0]: row["weight_pct"] for row in result["strata"]}
        self.assertEqual({"a": 25.0, "b": 75.0}, weights)


class ManifestTests(unittest.TestCase):
    def _files(self):
        directory = tempfile.TemporaryDirectory()
        data = Path(directory.name) / "review.csv"
        data.write_text("content\n")
        manifest = Path(directory.name) / "review.csv.manifest.json"
        manifest.write_text(json.dumps({
            "status": "complete",
            "requested_hours": 156,
            "completed_hours": 156,
            "block_start_hour_utc": 6,
            "block_hours": 12,
            "context_hours_per_block": 1,
            "months": [{} for _ in range(12)],
            "csv_bytes": data.stat().st_size,
            "csv_sha256": analyze_gha.file_sha256(data),
        }))
        return directory, data, manifest

    def test_matching_manifest_is_accepted(self):
        directory, data, manifest = self._files()
        self.addCleanup(directory.cleanup)
        result = analyze_gha.validate_manifest(data, manifest)
        self.assertEqual(156, result["completed_hours"])

    def test_mismatched_data_is_rejected(self):
        directory, data, manifest = self._files()
        self.addCleanup(directory.cleanup)
        data.write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "byte size|SHA-256"):
            analyze_gha.validate_manifest(data, manifest)

    def test_mismatched_block_design_is_rejected(self):
        for field, value, message in (
            ("block_start_hour_utc", 7, "block start"),
            ("block_hours", 11, "block duration"),
            ("context_hours_per_block", 0, "context hour"),
        ):
            with self.subTest(field=field):
                directory, data, manifest = self._files()
                self.addCleanup(directory.cleanup)
                payload = json.loads(manifest.read_text())
                payload[field] = value
                manifest.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError, message):
                    analyze_gha.validate_manifest(data, manifest)


if __name__ == "__main__":
    unittest.main()
