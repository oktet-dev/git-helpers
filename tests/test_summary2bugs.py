"""Tests for gitgo_summary2bugs -- extracting bug IDs from commit summaries."""

import pytest

from tests.conftest import run_gitgo


def summary2bugs(summary: str) -> str:
    r = run_gitgo("gitgo_summary2bugs", summary)
    assert r.returncode == 0
    return r.stdout.strip()


class TestJiraIssues:
    def test_single_jira(self) -> None:
        assert summary2bugs("BUG-123: fix the widget") == "BUG-123"

    def test_multiple_jira(self) -> None:
        result = summary2bugs("BUG-1: BUG-2: some message")
        assert "BUG-1" in result
        assert "BUG-2" in result

    def test_lowercase_jira_is_empty(self) -> None:
        # The grep uses uppercase-only pattern [A-Z]+-[0-9]+
        assert summary2bugs("bug-123: fix") == ""

    def test_jira_with_long_prefix(self) -> None:
        assert summary2bugs("PROJ-42: refactor") == "PROJ-42"


class TestLegacyBugTask:
    def test_bug_uppercase(self) -> None:
        assert summary2bugs("Bug 42: fix crash") == "42"

    def test_bug_lowercase(self) -> None:
        assert summary2bugs("bug 42: fix crash") == "42"

    def test_task(self) -> None:
        assert summary2bugs("Task 99: implement feature") == "99"

    def test_task_lowercase(self) -> None:
        assert summary2bugs("task 99: implement feature") == "99"


class TestGuardAndEmpty:
    def test_fixup_prefix_is_empty(self) -> None:
        # "fixup:" matches ^[A-Za-z0-9_-]+[A-Za-z_-]: => first branch => empty
        assert summary2bugs("fixup: some message") == ""

    def test_plain_message_is_empty(self) -> None:
        assert summary2bugs("plain message without bug") == ""

    def test_empty_input(self) -> None:
        assert summary2bugs("") == ""
