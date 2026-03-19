"""Tests for gg rbt-import -- populate reviews.db from existing RB series."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import GitRepo, RbtMock


def _write_api_mock(rbt_mock: RbtMock, reviews: dict[str, dict]) -> None:
    """Rewrite the mock rbt script to also handle `api-get` subcommand.

    reviews: {review_id: {"summary": str, "blocks": [review_id, ...]}}
    """
    log_file = rbt_mock.log_file
    script = rbt_mock.script_dir / "rbt"

    # Build a bash case statement for api-get responses
    api_cases = ""
    for rid, info in reviews.items():
        # Match real RB API format: blocks are link objects with href
        blocks = [
            {"href": f"https://rb.example.com/api/review-requests/{b}/", "method": "GET"}
            for b in info.get("blocks", [])
        ]
        target_people = [{"title": p} for p in info.get("target_people", [])]
        target_groups = [{"title": g} for g in info.get("target_groups", [])]
        resp = json.dumps({
            "review_request": {
                "id": int(rid),
                "summary": info["summary"],
                "blocks": blocks,
                "target_people": target_people,
                "target_groups": target_groups,
            }
        })
        api_cases += f'    */review-requests/{rid}/*)\n'
        api_cases += f"        echo '{resp}'\n"
        api_cases += "        ;;\n"

    script.write_text(f"""\
#!/usr/bin/env bash
LOG="{log_file}"
touch "$LOG"
COUNT=$(wc -l < "$LOG" | tr -d ' ')
ID=$(( COUNT + 1000 ))

ARGS_JSON=$(python3 -c "import sys, json; print(json.dumps(sys.argv[1:]))" "$@")
echo "$ARGS_JSON" >> "$LOG"

if [ "$1" = "api-get" ]; then
    case "$2" in
{api_cases}    *)
        echo '{{"stat": "fail"}}' >&2
        exit 1
        ;;
    esac
    exit 0
fi

echo "Review request #$ID posted."
echo "https://reviews.example.com/r/$ID/"
echo "https://reviews.example.com/r/$ID/diff/"
""")
    script.chmod(0o755)


class TestChainWalk:
    def test_three_review_chain(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Three reviews linked via blocks -> returns 3 IDs in order."""
        _write_api_mock(rbt_mock, {
            "100": {"summary": "first", "blocks": ["101"]},
            "101": {"summary": "second", "blocks": ["102"]},
            "102": {"summary": "third", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("first")
        git_repo.commit("second")
        git_repo.commit("third")

        r = git_repo.run_gg("rbt-import", "-d", "100")
        assert r.returncode == 0
        assert "r/100" in r.stdout
        assert "r/101" in r.stdout
        assert "r/102" in r.stdout

    def test_single_review_no_blocks(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Single review with no blocks -> returns 1 ID."""
        _write_api_mock(rbt_mock, {
            "200": {"summary": "solo fix", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("solo fix")

        r = git_repo.run_gg("rbt-import", "-d", "200")
        assert r.returncode == 0
        assert "r/200" in r.stdout

    def test_ambiguous_chain_errors(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Review blocking multiple reviews -> error."""
        _write_api_mock(rbt_mock, {
            "300": {"summary": "root", "blocks": ["301", "302"]},
            "301": {"summary": "branch a", "blocks": []},
            "302": {"summary": "branch b", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("root")

        r = git_repo.run_gg("rbt-import", "-d", "300")
        assert r.returncode != 0
        assert "Ambiguous chain" in r.stderr


class TestImport:
    def test_basic_import(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """3 commits + 3-review chain -> correct DB entries."""
        _write_api_mock(rbt_mock, {
            "400": {"summary": "fix crash", "blocks": ["401"]},
            "401": {"summary": "add tests", "blocks": ["402"]},
            "402": {"summary": "update docs", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        git_repo.commit("update docs")

        r = git_repo.run_gg("rbt-import", "400")
        assert r.returncode == 0
        assert "r/400" in r.stdout
        assert "r/401" in r.stdout
        assert "r/402" in r.stdout

    def test_extra_commits_skipped_by_subject(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """3 commits + 2-review chain -> matches by subject, skips extra."""
        _write_api_mock(rbt_mock, {
            "500": {"summary": "fix crash", "blocks": ["501"]},
            "501": {"summary": "add tests", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("unrelated refactor")
        git_repo.commit("add tests")

        r = git_repo.run_gg("rbt-import", "500")
        assert r.returncode == 0
        assert "r/500" in r.stdout
        assert "r/501" in r.stdout
        assert "Skipped" in r.stdout
        assert "unrelated refactor" in r.stdout

    def test_unmatched_review_errors(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Review with no matching commit -> error."""
        _write_api_mock(rbt_mock, {
            "510": {"summary": "exists", "blocks": ["511"]},
            "511": {"summary": "no such commit", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("exists")

        r = git_repo.run_gg("rbt-import", "510")
        assert r.returncode != 0
        assert "Unmatched reviews" in r.stderr

    def test_prefix_stripped_for_matching(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Review summaries with [N/M]: prefix match commits without it."""
        _write_api_mock(rbt_mock, {
            "520": {"summary": "[1/2]: fix crash", "blocks": ["521"]},
            "521": {"summary": "[2/2]: add tests", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")

        r = git_repo.run_gg("rbt-import", "520")
        assert r.returncode == 0
        assert "r/520" in r.stdout
        assert "r/521" in r.stdout

    def test_dry_run_no_db_write(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Dry run prints table but DB stays empty."""
        _write_api_mock(rbt_mock, {
            "600": {"summary": "fix crash", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")

        r = git_repo.run_gg("rbt-import", "-d", "600")
        assert r.returncode == 0

        # Verify DB is empty -- rbt-sync should fail
        r2 = git_repo.run_gg("rbt-sync", "-d")
        assert r2.returncode == 1
        assert "No existing reviews" in r2.stdout

    def test_overwrite_warning(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Importing over existing entries warns on stderr."""
        _write_api_mock(rbt_mock, {
            "700": {"summary": "fix crash", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")

        # First import
        r = git_repo.run_gg("rbt-import", "700")
        assert r.returncode == 0

        # Second import overwrites
        r2 = git_repo.run_gg("rbt-import", "700")
        assert r2.returncode == 0
        assert "overwriting" in r2.stderr.lower()

    def test_sync_after_import(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Import then rbt-sync -d should show all 'keep'."""
        _write_api_mock(rbt_mock, {
            "800": {"summary": "fix crash", "blocks": ["801"]},
            "801": {"summary": "add tests", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")

        r = git_repo.run_gg("rbt-import", "800")
        assert r.returncode == 0

        r2 = git_repo.run_gg("rbt-sync", "-d")
        assert r2.returncode == 0
        keep_lines = [l for l in r2.stdout.splitlines() if "keep" in l]
        assert len(keep_lines) == 2

    def test_no_commits_in_range(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """No commits in range -> exit 1."""
        _write_api_mock(rbt_mock, {
            "900": {"summary": "whatever", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        # No commits on this branch

        r = git_repo.run_gg("rbt-import", "900")
        assert r.returncode == 1
        assert "No commits" in r.stdout

    def test_custom_range(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--range flag selects a subset of commits."""
        _write_api_mock(rbt_mock, {
            "950": {"summary": "second", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        rev1 = git_repo.commit("first")
        rev2 = git_repo.commit("second")

        # Import only the last commit
        r = git_repo.run_gg("rbt-import", "--range", f"{rev1}..{rev2}", "950")
        assert r.returncode == 0
        assert "r/950" in r.stdout
        # Only 1 entry
        lines = [l for l in r.stdout.splitlines() if "r/950" in l]
        assert len(lines) == 1


class TestImportReviewers:
    def test_import_shows_reviewers(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Import output includes reviewers from the first review."""
        _write_api_mock(rbt_mock, {
            "960": {
                "summary": "fix crash",
                "blocks": ["961"],
                "target_people": ["alice", "bob"],
                "target_groups": ["devteam"],
            },
            "961": {"summary": "add tests", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")

        r = git_repo.run_gg("rbt-import", "-d", "960")
        assert r.returncode == 0
        assert "Reviewers: alice, bob" in r.stdout
        assert "Groups: devteam" in r.stdout

    def test_import_no_reviewers_no_header(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Import without reviewers omits the header."""
        _write_api_mock(rbt_mock, {
            "970": {"summary": "fix crash", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")

        r = git_repo.run_gg("rbt-import", "-d", "970")
        assert r.returncode == 0
        assert "Reviewers" not in r.stdout
        assert "Groups" not in r.stdout
