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
        blocks_json = json.dumps([{"id": int(b)} for b in info.get("blocks", [])])
        resp = json.dumps({
            "review_request": {
                "id": int(rid),
                "summary": info["summary"],
                "blocks": json.loads(blocks_json),
            }
        })
        api_cases += f'    */api/review-requests/{rid}/*)\n'
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

    def test_count_mismatch(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """2 commits but 3 reviews -> exit 1."""
        _write_api_mock(rbt_mock, {
            "500": {"summary": "first", "blocks": ["501"]},
            "501": {"summary": "second", "blocks": ["502"]},
            "502": {"summary": "third", "blocks": []},
        })

        git_repo.create_branch("feature", "master")
        git_repo.commit("first")
        git_repo.commit("second")

        r = git_repo.run_gg("rbt-import", "500")
        assert r.returncode == 1
        assert "2 commits" in r.stderr
        assert "3 reviews" in r.stderr

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
            "950": {"summary": "second only", "blocks": []},
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
