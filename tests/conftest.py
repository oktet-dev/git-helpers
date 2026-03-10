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


_MOCK_RBT_SCRIPT = """\
#!/usr/bin/env python3
import json
import os
import re
import sys

_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
with open(os.path.join(_dir, "config.json")) as _f:
    _cfg = json.load(_f)

LOG = _cfg["log"]
STATE_DIR = _cfg["state"]
os.makedirs(STATE_DIR, exist_ok=True)

if not os.path.exists(LOG):
    open(LOG, "w").close()

with open(LOG) as f:
    count = sum(1 for _ in f)
rid = count + 1000

with open(LOG, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")

cmd = sys.argv[1] if len(sys.argv) > 1 else ""

if cmd == "api-get":
    m = re.search(r"(\\d+)", sys.argv[2] if len(sys.argv) > 2 else "")
    review_id = m.group(1) if m else "0"
    state_file = os.path.join(STATE_DIR, review_id + ".json")
    state = {}
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)
    rr = {
        "id": int(review_id),
        "summary": state.get("summary", ""),
        "blocks": [],
        "target_people": [{"title": p} for p in state.get("people", [])],
        "target_groups": [{"title": g} for g in state.get("groups", [])],
    }
    print(json.dumps({"review_request": rr}))

elif cmd == "close":
    print("Discarded review request.")

else:
    args = sys.argv[2:]
    people = []
    groups = []
    summary = ""
    i = 0
    while i < len(args):
        if args[i] == "--target-people" and i + 1 < len(args):
            people.append(args[i + 1])
            i += 2
        elif args[i] == "--target-groups" and i + 1 < len(args):
            groups.append(args[i + 1])
            i += 2
        elif args[i].startswith("--summary="):
            summary = args[i].split("=", 1)[1]
            i += 1
        else:
            i += 1
    state = {"people": people, "groups": groups, "summary": summary}
    state_file = os.path.join(STATE_DIR, str(rid) + ".json")
    with open(state_file, "w") as f:
        json.dump(state, f)

    print(f"Review request #{rid} posted.")
    print(f"https://reviews.example.com/r/{rid}/")
    print(f"https://reviews.example.com/r/{rid}/diff/")
"""


@pytest.fixture
def rbt_mock(tmp_path: Path) -> RbtMock:
    """Create a mock rbt script that logs calls and prints fake review IDs."""
    mock_dir = tmp_path / "rbt_mock_bin"
    mock_dir.mkdir()
    log_file = tmp_path / "rbt_calls.log"
    state_dir = tmp_path / "rbt_state"

    config = {"log": str(log_file), "state": str(state_dir)}
    (mock_dir / "config.json").write_text(json.dumps(config))

    script = mock_dir / "rbt"
    script.write_text(_MOCK_RBT_SCRIPT)
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
