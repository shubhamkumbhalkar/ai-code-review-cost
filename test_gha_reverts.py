import csv
import datetime as dt
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))
import gha_reverts

UTC = dt.timezone.utc


class RevertReferenceTests(unittest.TestCase):
    def test_explicit_reference_forms(self):
        cases = [
            ("Reverts #123.", {("owner/project", 123)}),
            ("Reverts owner/project#123.", {("owner/project", 123)}),
            ("Reverts https://github.com/owner/project/pull/123.", {("owner/project", 123)}),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(
                    expected,
                    gha_reverts.extract_revert_pr_refs(
                        "owner/project", 457, 'Revert "unsafe change"', body
                    ),
                )

    def test_ambiguous_cross_repo_and_self_references_are_ignored(self):
        cases = [
            ("Fix rollback", "Revert behavior in #123."),
            ('Revert "unsafe" (#457)', ""),
            ('Revert "unsafe"', "Reverts #457."),
            ('Revert "unsafe"', "Reverts other/project#123."),
        ]
        for title, body in cases:
            with self.subTest(title=title, body=body):
                self.assertEqual(
                    set(),
                    gha_reverts.extract_revert_pr_refs(
                        "owner/project", 457, title, body
                    ),
                )


class TimestampedEvidenceTests(unittest.TestCase):
    def test_full_and_short_sha_require_same_repo_and_branch(self):
        full_sha = "a" * 40
        long_sha = "abcdef0123456789abcdef0123456789abcdef01"
        observed = dt.datetime(2025, 8, 2, tzinfo=UTC)
        full, short = {}, {}
        gha_reverts.record_revert_shas(
            "owner/project",
            "refs/heads/main",
            f"This reverts commit {full_sha}.\nThis reverts commit abcdef0.",
            observed,
            full,
            short,
        )
        self.assertEqual(
            observed, full[("owner/project", full_sha, "refs/heads/main")]
        )
        self.assertEqual(
            observed,
            short[("owner/project", "refs/heads/main")]["abcdef0"],
        )
        self.assertEqual(
            observed,
            gha_reverts.revert_timestamp(
                "owner/project", "main", long_sha, 12,
                "2025-08-01T00:00:00Z", full, short, {},
            ),
        )
        for repo, branch in (("other/project", "main"), ("owner/project", "feature")):
            with self.subTest(repo=repo, branch=branch):
                self.assertIsNone(
                    gha_reverts.revert_timestamp(
                        repo, branch, long_sha, 12,
                        "2025-08-01T00:00:00Z", full, short, {},
                    )
                )

    def test_short_sha_lookup_does_not_scan_global_index(self):
        class NoGlobalScan(dict):
            def items(self):
                raise AssertionError("global short-SHA scan is forbidden")

        sha = "abcdef0123456789abcdef0123456789abcdef01"
        observed = dt.datetime(2025, 8, 2, tzinfo=UTC)
        short = NoGlobalScan({
            ("owner/project", "refs/heads/main"): {"abcdef0": observed}
        })
        self.assertEqual(
            observed,
            gha_reverts.revert_timestamp(
                "owner/project", "main", sha, 12,
                "2025-08-01T00:00:00Z", {}, short, {},
            ),
        )

    def test_pre_merge_evidence_is_rejected(self):
        sha = "a" * 40
        full = {
            ("owner/project", sha, "refs/heads/main"):
                dt.datetime(2025, 8, 1, tzinfo=UTC)
        }
        self.assertIsNone(
            gha_reverts.revert_timestamp(
                "owner/project", "main", sha, 1,
                "2025-08-02T00:00:00Z", full, {}, {},
            )
        )

    def test_earliest_same_branch_evidence_wins(self):
        sha = "a" * 40
        full_time = dt.datetime(2025, 8, 5, tzinfo=UTC)
        pr_time = dt.datetime(2025, 8, 3, tzinfo=UTC)
        self.assertEqual(
            pr_time,
            gha_reverts.revert_timestamp(
                "owner/project", "main", sha, 1,
                "2025-08-01T00:00:00Z",
                {("owner/project", sha, "refs/heads/main"): full_time},
                {},
                {("owner/project", 1, "main"): pr_time},
            ),
        )


class LinkageTests(unittest.TestCase):
    def test_head_and_merge_sha_timing_rules(self):
        sha = "a" * 40
        before = dt.datetime(2025, 8, 1, 0, 30, tzinfo=UTC)
        within_grace = dt.datetime(2025, 8, 1, 1, 59, tzinfo=UTC)
        after_grace = dt.datetime(2025, 8, 1, 2, 0, 1, tzinfo=UTC)
        merged = "2025-08-01T01:00:00Z"
        self.assertEqual(
            (1, 1),
            gha_reverts.classify_pull_request(
                sha, "b" * 40, {sha: before},
                {sha: {"observed_at": before, "tools": {"claude"}}}, merged,
            ),
        )
        self.assertEqual(
            (1, 1),
            gha_reverts.classify_pull_request(
                "c" * 40, sha, {sha: within_grace},
                {sha: {"observed_at": within_grace, "tools": {"claude"}}}, merged,
            ),
        )
        self.assertEqual(
            (0, 0),
            gha_reverts.classify_pull_request(
                "c" * 40, sha, {sha: after_grace},
                {sha: {"observed_at": after_grace, "tools": {"claude"}}}, merged,
            ),
        )


class ProcessHourTests(unittest.TestCase):
    def _archive(self, events):
        handle = tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False)
        handle.close()
        path = Path(handle.name)
        with gzip.open(path, "wt") as archive:
            for event in events:
                archive.write(json.dumps(event) + "\n")
        return path

    @staticmethod
    def _pull(
        number, created, merged, title="change", body="", base="main",
        is_merged=True, action="closed",
    ):
        return {
            "type": "PullRequestEvent",
            "created_at": merged,
            "repo": {"name": "owner/project"},
            "payload": {
                "action": action,
                "pull_request": {
                    "number": number,
                    "merged": is_merged,
                    "created_at": created,
                    "merged_at": merged,
                    "merge_commit_sha": (str(number) * 40)[:40],
                    "head": {"sha": (chr(96 + number) * 40)[:40]},
                    "base": {"ref": base},
                    "user": {"type": "User"},
                    "commits": 1,
                    "additions": 8,
                    "deletions": 2,
                    "changed_files": 1,
                    "comments": 0,
                    "author_association": "OWNER",
                    "title": title,
                    "body": body,
                },
            },
        }

    @staticmethod
    def _push(created, sha, message, ref="refs/heads/main", payload_head=None):
        return {
            "type": "PushEvent",
            "created_at": created,
            "repo": {"name": "owner/project"},
            "payload": {
                "ref": ref,
                "head": payload_head or sha,
                "commits": [{"sha": sha, "message": message}] if message is not None else [],
            },
        }

    def _process(self, events, future=None):
        archive = self._archive(events)
        state = ({}, {}, {}, {}, {}, {}, set())
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as handle:
            rows_path = Path(handle.name)
            with patch.object(gha_reverts, "download_hour", return_value=(archive, 2_000_000)):
                result, next_future = gha_reverts.process_hour(
                    "2025-08-01-0", handle, *state, future or {}
                )
            handle.flush()
        rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
        rows_path.unlink(missing_ok=True)
        return result, next_future, rows, state

    def test_missing_or_null_size_is_unknown(self):
        base = {
            "number": 1,
            "created_at": "2025-08-01T00:00:00Z",
            "merged_at": "2025-08-01T00:10:00Z",
        }
        self.assertIsNone(
            gha_reverts._pull_request_record("owner/project", base)["pr_size"]
        )
        with_null = dict(base, additions=None, deletions=3)
        self.assertIsNone(
            gha_reverts._pull_request_record("owner/project", with_null)["pr_size"]
        )

    def test_pr_first_processing_and_merged_revert_pr(self):
        source_sha = "a" * 40
        events = [
            self._push("2025-08-01T00:20:00Z", source_sha, "Generated with Claude Code"),
            self._pull(1, "2025-08-01T00:00:00Z", "2025-08-01T00:40:00Z"),
            self._pull(
                3, "2025-08-01T00:45:00Z", "2025-08-01T00:55:00Z",
                'Revert "change"', "Reverts #1.",
            ),
        ]
        result, _, rows, state = self._process(events)
        target, linked, ai, full, short, refs, seen = state
        self.assertEqual(2, result["merged_prs"])
        self.assertEqual(2, len(rows))
        self.assertEqual((1, 1), gha_reverts.classify_pull_request(
            source_sha, rows[0]["merge_sha"], linked, ai, rows[0]["merged_at"]
        ))
        self.assertEqual(
            dt.datetime(2025, 8, 1, 0, 55, tzinfo=UTC),
            refs[("owner/project", 1, "main")],
        )

    def test_duplicate_pull_request_across_hours_is_written_once(self):
        event = self._pull(
            1, "2025-08-01T00:00:00Z", "2025-08-01T00:40:00Z"
        )
        archives = [self._archive([event]), self._archive([event])]
        state = ({}, {}, {}, {}, {}, {}, set())
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as handle:
            rows_path = Path(handle.name)
            with patch.object(
                gha_reverts, "download_hour",
                side_effect=[(archives[0], 2_000_000), (archives[1], 2_000_000)],
            ):
                _, future = gha_reverts.process_hour(
                    "2025-08-01-1", handle, *state, {}
                )
                gha_reverts.process_hour(
                    "2025-08-01-0", handle, *state, future
                )
            handle.flush()
        rows = rows_path.read_text().splitlines()
        rows_path.unlink(missing_ok=True)
        self.assertEqual(1, len(rows))
        self.assertEqual(1, len(state[-1]))

    def test_feature_branch_revert_commit_does_not_match_main(self):
        merge_sha = "1" * 40
        events = [
            self._pull(1, "2025-08-01T00:00:00Z", "2025-08-01T00:10:00Z"),
            self._push(
                "2025-08-01T00:20:00Z", "f" * 40,
                f"This reverts commit {merge_sha}.", "refs/heads/feature",
            ),
        ]
        _, _, rows, state = self._process(events)
        full, short, refs = state[3], state[4], state[5]
        self.assertIsNone(gha_reverts.revert_timestamp(
            "owner/project", "main", merge_sha, 1, rows[0]["merged_at"],
            full, short, refs,
        ))

    def test_unmerged_revert_pull_request_does_not_count(self):
        events = [
            self._pull(1, "2025-08-01T00:00:00Z", "2025-08-01T00:10:00Z"),
            self._pull(
                3, "2025-08-01T00:20:00Z", "2025-08-01T00:30:00Z",
                'Revert "change"', "Reverts #1.", is_merged=False,
            ),
        ]
        _, _, rows, state = self._process(events)
        self.assertEqual(1, len(rows))
        self.assertNotIn(("owner/project", 1, "main"), state[5])

    def test_revert_pull_request_on_different_base_does_not_count(self):
        events = [
            self._pull(1, "2025-08-01T00:00:00Z", "2025-08-01T00:10:00Z"),
            self._pull(
                3, "2025-08-01T00:20:00Z", "2025-08-01T00:30:00Z",
                'Revert "change"', "Reverts #1.", base="release",
            ),
        ]
        _, _, rows, state = self._process(events)
        self.assertIsNone(gha_reverts.revert_timestamp(
            "owner/project", "main", rows[0]["merge_sha"], 1,
            rows[0]["merged_at"], state[3], state[4], state[5],
        ))
        self.assertIn(("owner/project", 1, "release"), state[5])

    def test_payload_head_links_commit_missing_from_commit_list(self):
        source_sha = "a" * 40
        events = [
            self._pull(1, "2025-08-01T00:00:00Z", "2025-08-01T00:40:00Z"),
            self._push(
                "2025-08-01T00:20:00Z", "x" * 40, None,
                payload_head=source_sha,
            ),
        ]
        _, _, rows, state = self._process(events)
        linked = state[1]
        self.assertEqual((1, 0), gha_reverts.classify_pull_request(
            source_sha, rows[0]["merge_sha"], linked, {}, rows[0]["merged_at"]
        ))

    def test_process_hour_returns_current_push_cache(self):
        sha = "d" * 40
        observed = "2025-08-01T00:20:00Z"
        _, next_future, _, _ = self._process([
            self._push(observed, sha, "Generated with Claude Code")
        ])
        self.assertEqual(
            dt.datetime(2025, 8, 1, 0, 20, tzinfo=UTC),
            next_future[sha]["observed_at"],
        )
        self.assertEqual({"claude"}, next_future[sha]["tools"])

    def test_cross_hour_merge_push_and_duplicate_dedup(self):
        event = self._pull(
            1, "2025-08-01T00:00:00Z", "2025-08-01T00:50:00Z"
        )
        merge_sha = "1" * 40
        future_time = dt.datetime(2025, 8, 1, 1, 10, tzinfo=UTC)
        result, _, rows, state = self._process(
            [event, event],
            {merge_sha: {"observed_at": future_time, "tools": {"claude"}}},
        )
        self.assertEqual(1, result["merged_prs"])
        self.assertEqual(1, len(rows))
        self.assertEqual(future_time, state[1][merge_sha])
        self.assertEqual({"claude"}, state[2][merge_sha]["tools"])
    def test_cross_hour_merge_push_beyond_grace_is_rejected(self):
        event = self._pull(
            1, "2025-08-01T00:00:00Z", "2025-08-01T00:50:00Z"
        )
        merge_sha = "1" * 40
        too_late = dt.datetime(2025, 8, 1, 1, 50, 1, tzinfo=UTC)
        _, _, rows, state = self._process(
            [event],
            {merge_sha: {"observed_at": too_late, "tools": {"claude"}}},
        )
        self.assertNotIn(merge_sha, state[1])
        self.assertEqual((0, 0), gha_reverts.classify_pull_request(
            rows[0]["head_sha"], merge_sha, state[1], state[2], rows[0]["merged_at"]
        ))


class CollectionFlowTests(unittest.TestCase):
    def test_atomic_json_writer_replaces_complete_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.manifest.json"
            path.write_text('{"old": true}\n')
            gha_reverts._write_json_atomic(path, {"status": "complete"})
            self.assertEqual({"status": "complete"}, json.loads(path.read_text()))
            self.assertEqual([], list(Path(directory).glob("result.manifest.json.*")))

    def test_collection_passes_only_immediately_newer_push_cache(self):
        calls = []

        def fake_process(hour, handle, target, linked, ai, full, short, refs, seen, future):
            calls.append((hour, set(future)))
            marker = {
                hour: {
                    "observed_at": dt.datetime(2025, 8, 1, tzinfo=UTC),
                    "tools": set(),
                }
            }
            return ({"hour": hour, "bytes": 2_000_000, "events": 1, "merged_prs": 0}, marker)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reverts.csv"
            with patch.object(gha_reverts, "process_hour", side_effect=fake_process):
                gha_reverts.collect("2025-08-01", 15, output)
        self.assertEqual(set(), calls[0][1])
        self.assertEqual({calls[0][0]}, calls[1][1])
        self.assertEqual({calls[1][0]}, calls[2][1])
        self.assertNotIn(calls[0][0], calls[2][1])

    def test_collection_writes_branch_and_timestamp_schema(self):
        calls = []

        def fake_process(hour, handle, target, linked, ai, full, short, refs, seen, future):
            calls.append(hour)
            if len(calls) == 1:
                seen.add(("owner/project", 1))
                handle.write(json.dumps({
                    "repo": "owner/project", "pr": 1, "base_ref": "main",
                    "merge_sha": "b" * 40, "head_sha": "a" * 40,
                    "time_to_merge_h": 1.0, "commits": 1, "pr_size": 10,
                    "files_changed": 1, "comments": 0, "author_assoc": "OWNER",
                    "author_type": "User", "merged_at": "2025-08-01T01:00:00Z",
                }) + "\n")
                linked["a" * 40] = dt.datetime(2025, 8, 1, 0, 30, tzinfo=UTC)
                full[("owner/project", "b" * 40, "refs/heads/main")] = dt.datetime(
                    2025, 8, 2, tzinfo=UTC
                )
            return ({"hour": hour, "bytes": 2_000_000, "events": 10, "merged_prs": 1}, {})

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reverts.csv"
            output.write_text("old data\n")
            with patch.object(gha_reverts, "process_hour", side_effect=fake_process):
                manifest = gha_reverts.collect("2025-08-01", 15, output)
            self.assertEqual("2025-08-15-23", calls[0])
            self.assertEqual("2025-08-01-0", calls[-1])
            with output.open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual("main", rows[0]["base_ref"])
            self.assertEqual("1", rows[0]["reverted"])
            self.assertEqual("2025-08-02T00:00:00Z", rows[0]["reverted_at"])
            saved = json.loads(output.with_suffix(".csv.manifest.json").read_text())
            self.assertEqual(360, saved["completed_hours"])
            self.assertEqual(output.stat().st_size, saved["csv_bytes"])
            self.assertEqual(gha_reverts.file_sha256(output), saved["csv_sha256"])
            self.assertEqual(1, manifest["pull_requests"])

    def test_csv_failure_preserves_output_and_removes_temporary_files(self):
        calls = 0

        def fake_process(hour, handle, target, linked, ai, full, short, refs, seen, future):
            nonlocal calls
            calls += 1
            if calls == 1:
                handle.write(json.dumps({
                    "repo": "owner/project", "pr": 1, "base_ref": "main",
                    "merge_sha": "b" * 40, "head_sha": "a" * 40,
                    "time_to_merge_h": 1.0, "commits": 1, "pr_size": 10,
                    "files_changed": 1, "comments": 0, "author_assoc": "OWNER",
                    "author_type": "User", "merged_at": "2025-08-01T01:00:00Z",
                }) + "\n")
            return ({"hour": hour, "bytes": 2_000_000, "events": 1, "merged_prs": 0}, {})

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reverts.csv"
            output.write_text("existing validated data\n")
            with patch.object(gha_reverts, "process_hour", side_effect=fake_process), patch.object(
                gha_reverts, "revert_timestamp", side_effect=RuntimeError("csv failure")
            ):
                with self.assertRaisesRegex(RuntimeError, "csv failure"):
                    gha_reverts.collect("2025-08-01", 15, output)
            self.assertEqual("existing validated data\n", output.read_text())
            self.assertEqual([], list(Path(directory).glob("gha_reverts_*")))

    def test_replace_failure_preserves_output_and_removes_temporary_csv(self):
        def fake_process(hour, handle, target, linked, ai, full, short, refs, seen, future):
            return ({"hour": hour, "bytes": 2_000_000, "events": 1, "merged_prs": 0}, {})

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reverts.csv"
            output.write_text("existing validated data\n")
            with patch.object(gha_reverts, "process_hour", side_effect=fake_process), patch.object(
                gha_reverts.os, "replace", side_effect=RuntimeError("replace failure")
            ):
                with self.assertRaisesRegex(RuntimeError, "replace failure"):
                    gha_reverts.collect("2025-08-01", 15, output)
            self.assertEqual("existing validated data\n", output.read_text())
            self.assertEqual([], list(Path(directory).glob("gha_reverts_*.csv")))

    def test_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reverts.csv"
            output.write_text("existing validated data\n")
            with patch.object(
                gha_reverts, "process_hour",
                side_effect=RuntimeError("simulated missing hour"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated missing hour"):
                    gha_reverts.collect("2025-08-01", 15, output)
            self.assertEqual("existing validated data\n", output.read_text())
            self.assertFalse(output.with_suffix(".csv.manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
