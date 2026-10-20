"""Tests for install.sh flags that need no network."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _install(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "install.sh", *args],
        cwd=REPO,
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )


class TestUsage:
    def test_help_lists_the_options(self, tmp_path: Path) -> None:
        for flag in ("-h", "--help"):
            r = _install(tmp_path, flag)
            assert r.returncode == 0, r.stderr
            assert r.stdout.startswith("Usage: ./install.sh")
            for option in ("--gg", "--skill", "--help"):
                assert option in r.stdout
        assert not (tmp_path / ".bashrc.gitgo").exists()

    def test_unknown_option_installs_nothing(self, tmp_path: Path) -> None:
        r = _install(tmp_path, "--skil")
        assert r.returncode == 1
        assert "Unknown option: --skil" in r.stderr
        assert "Usage: ./install.sh" in r.stderr
        assert not (tmp_path / ".bashrc.gitgo").exists()


class TestSkillFlag:
    def test_links_the_skill(self, tmp_path: Path) -> None:
        r = _install(tmp_path, "--skill")
        assert r.returncode == 0, r.stderr
        link = tmp_path / ".claude" / "skills" / "gg"
        assert link.is_symlink()
        assert link.resolve() == REPO / "skills" / "gg"
        assert (link / "SKILL.md").read_text().startswith("---\nname: gg\n")

    def test_replaces_an_old_link(self, tmp_path: Path) -> None:
        skills = tmp_path / ".claude" / "skills"
        skills.mkdir(parents=True)
        (skills / "gg").symlink_to(tmp_path)
        r = _install(tmp_path, "--skill")
        assert r.returncode == 0, r.stderr
        assert (skills / "gg").resolve() == REPO / "skills" / "gg"

    def test_keeps_a_real_directory(self, tmp_path: Path) -> None:
        own = tmp_path / ".claude" / "skills" / "gg"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("mine\n")
        r = _install(tmp_path, "--skill")
        assert r.returncode == 1
        assert "not a symlink" in r.stderr
        assert (own / "SKILL.md").read_text() == "mine\n"
