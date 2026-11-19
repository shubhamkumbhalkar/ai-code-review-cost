import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "code"))
import detect_ai_commits


class DetectorTests(unittest.TestCase):
    def test_claude_generated_marker_is_not_double_labeled_generic(self):
        message = "Fix parser\n\n🤖 Generated with [Claude Code](https://claude.ai/code)"
        self.assertEqual({"claude"}, detect_ai_commits.classify(message))

    def test_claude_noreply_trailer_matches(self):
        message = "Co-authored-by: Claude <noreply@anthropic.com>"
        self.assertEqual({"claude"}, detect_ai_commits.classify(message))

    def test_person_named_claude_does_not_match(self):
        message = "Co-authored-by: Claude Smith <claude@example.com>"
        self.assertEqual(set(), detect_ai_commits.classify(message))

    def test_generic_agent_marker_is_excluded_from_primary_label(self):
        message = "Co-authored-by: build agent <agent@example.com>"
        self.assertEqual(set(), detect_ai_commits.classify(message))

    def test_verified_devin_identity_matches_but_human_devin_does_not(self):
        bot = (
            "Co-Authored-By: Devin "
            "<158243242+devin-ai-integration[bot]@users.noreply.github.com>"
        )
        human = "Co-authored-by: Devin Smith <devin@example.com>"
        self.assertEqual({"devin"}, detect_ai_commits.classify(bot))
        self.assertEqual(set(), detect_ai_commits.classify(human))

    def test_verified_codex_identity_matches(self):
        self.assertEqual(
            {"codex"},
            detect_ai_commits.classify(
                "Co-authored-by: Codex <codex@openai.com>"
            ),
        )

    def test_unverified_aider_display_name_is_excluded(self):
        self.assertEqual(
            set(), detect_ai_commits.classify("Co-authored-by: Aider <person@example.com>")
        )

    def test_multiple_named_tools_are_retained(self):
        message = (
            "Co-authored-by: GitHub Copilot "
            "<223556219+Copilot@users.noreply.github.com>\n"
            "Co-authored-by: Cursor Agent <cursoragent@example.com>"
        )
        self.assertEqual({"copilot", "cursor"}, detect_ai_commits.classify(message))

    def test_bare_vendor_words_do_not_match(self):
        self.assertEqual(set(), detect_ai_commits.classify("Move cursor and ask Claude later"))


if __name__ == "__main__":
    unittest.main()
