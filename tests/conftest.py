from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASHRC_GITGO = REPO_ROOT / "bashrc.gitgo"


def _run(
    cmd: list[str] | str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    shell: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        shell=shell,
    )


@dataclass
class RbtMock:
    """Mock rbt executable that logs invocations."""

    script_dir: Path
    log_file: Path

    def calls(self) -> list[list[str]]:
        if not self.log_file.exists():
            return []
        lines = self.log_file.read_text().strip().splitlines()
        return [json.loads(line) for line in lines]

    def call(self, n: int) -> list[str]:
        return self.calls()[n]

    def call_count(self) -> int:
        return len(self.calls())


@dataclass
class GitRepo:
    """Temporary git repo with a bare origin and a working clone."""

    work_dir: Path
    origin_dir: Path
    _env: dict[str, str] = field(default_factory=dict)

    def git(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return _run(
            ["git", *args],
            cwd=self.work_dir,
            env=self._env,
            stdin=stdin,
        )

    def run_gitgo(
        self,
        func: str,
        *args: str,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = {**self._env, **(env or {})}
        quoted_args = " ".join(f"'{a}'" for a in args)
        script = f"source '{BASHRC_GITGO}'; {func} {quoted_args}"
        return _run(
            ["bash", "-c", script],
            cwd=self.work_dir,
            env=merged_env,
            stdin=stdin,
        )

    def commit(self, msg: str, filename: str | None = None) -> str:
        """Create a commit with an empty file and return its short hash."""
        fname = filename or msg.replace(" ", "_").replace(":", "")[:40]
        (self.work_dir / fname).write_text(msg + "\n")
        self.git("add", fname)
        self.git("commit", "-m", msg)
        return self.git("rev-parse", "--short", "HEAD").stdout.strip()

    def create_branch(self, name: str, track: str = "master") -> None:
        self.git("checkout", "-b", name, "--track", track)

    def run_gg(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run the gg CLI tool in this repo's working directory."""
        return _run(
            [sys.executable, "-m", "gg", *args],
            cwd=self.work_dir,
            env=self._env,
        )


def _make_env(extra_path: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    # Ensure reproducible git behavior
    env["GIT_AUTHOR_NAME"] = "Test User"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test User"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    # Disable pager
    env["GIT_PAGER"] = "cat"
    # Avoid terminal capabilities issues in CI
    env["TERM"] = "dumb"
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
    return env


@pytest.fixture
def rbt_mock(tmp_path: Path) -> RbtMock:
    """Create a mock rbt script that logs calls and prints fake review IDs."""
    mock_dir = tmp_path / "rbt_mock_bin"
    mock_dir.mkdir()
    log_file = tmp_path / "rbt_calls.log"

    script = mock_dir / "rbt"
    script.write_text(f"""\
#!/usr/bin/env bash
# Auto-incrementing review ID based on line count
LOG="{log_file}"
touch "$LOG"
COUNT=$(wc -l < "$LOG" | tr -d ' ')
ID=$(( COUNT + 1000 ))

# Log the call as JSON
ARGS_JSON=$(python3 -c "import sys, json; print(json.dumps(sys.argv[1:]))" "$@")
echo "$ARGS_JSON" >> "$LOG"

echo "Review request #$ID posted."
echo "https://reviews.example.com/r/$ID/"
echo "https://reviews.example.com/r/$ID/diff/"
""")
    script.chmod(0o755)

    return RbtMock(script_dir=mock_dir, log_file=log_file)


@pytest.fixture
def git_repo(tmp_path: Path, rbt_mock: RbtMock) -> GitRepo:
    """Create a bare origin + working clone with initial commit on master."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"

    env = _make_env(extra_path=str(rbt_mock.script_dir))

    # Create bare origin
    _run(["git", "init", "--bare", str(origin)], env=env)

    # Clone, make initial commit, push
    _run(["git", "clone", str(origin), str(work)], env=env)
    _run(["git", "checkout", "-b", "master"], cwd=work, env=env)

    initial_file = work / "README"
    initial_file.write_text("initial\n")
    _run(["git", "add", "README"], cwd=work, env=env)
    _run(["git", "commit", "-m", "initial commit"], cwd=work, env=env)
    _run(["git", "push", "-u", "origin", "master"], cwd=work, env=env)

    return GitRepo(work_dir=work, origin_dir=origin, _env=env)


def run_gitgo(
    func: str,
    *args: str,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a bash function from bashrc.gitgo without a git repo context."""
    merged_env = {**_make_env(), **(env or {})}
    quoted_args = " ".join(f"'{a}'" for a in args)
    script = f"source '{BASHRC_GITGO}'; {func} {quoted_args}"
    return _run(
        ["bash", "-c", script],
        env=merged_env,
        stdin=stdin,
    )
