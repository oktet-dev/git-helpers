"""Tests for gg.rb_links -- links to review requests."""

from pathlib import Path

import pytest

from gg import rb_links
from gg.rb_links import Links
from tests.conftest import GitRepo


@pytest.fixture
def no_home_rc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty home and no $RBTOOLS_CONFIG_PATH, so only the test's files count."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("RBTOOLS_CONFIG_PATH", raising=False)
    return home


def _rc(directory: Path, url: str) -> None:
    (directory / ".reviewboardrc").write_text(
        f'REVIEWBOARD_URL = "{url}"\nREPOSITORY = "repo"\n'
    )


class TestServerUrl:
    def test_from_repo_file(self, git_repo: GitRepo, no_home_rc: Path) -> None:
        _rc(git_repo.work_dir, "https://rb.example.com/")
        assert rb_links.server_url(cwd=git_repo.work_dir) == "https://rb.example.com/"

    def test_nearest_file_wins(self, git_repo: GitRepo, no_home_rc: Path) -> None:
        _rc(git_repo.work_dir, "https://outer.example.com/")
        sub = git_repo.work_dir / "sub"
        sub.mkdir()
        _rc(sub, "https://inner.example.com/")
        assert rb_links.server_url(cwd=sub) == "https://inner.example.com/"

    def test_config_path_before_repo_file(
        self, git_repo: GitRepo, no_home_rc: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _rc(git_repo.work_dir, "https://repo.example.com/")
        config_dir = tmp_path / "rbconf"
        config_dir.mkdir()
        _rc(config_dir, "https://env.example.com/")
        monkeypatch.setenv("RBTOOLS_CONFIG_PATH", str(config_dir))
        assert rb_links.server_url(cwd=git_repo.work_dir) == "https://env.example.com/"

    def test_home_file(self, git_repo: GitRepo, no_home_rc: Path) -> None:
        _rc(no_home_rc, "https://home.example.com/")
        assert rb_links.server_url(cwd=git_repo.work_dir) == "https://home.example.com/"

    def test_file_without_url_is_skipped(self, git_repo: GitRepo, no_home_rc: Path) -> None:
        (git_repo.work_dir / ".reviewboardrc").write_text('REPOSITORY = "repo"\n')
        _rc(no_home_rc, "https://home.example.com/")
        assert rb_links.server_url(cwd=git_repo.work_dir) == "https://home.example.com/"

    def test_broken_file_is_skipped(self, git_repo: GitRepo, no_home_rc: Path) -> None:
        (git_repo.work_dir / ".reviewboardrc").write_text("REVIEWBOARD_URL = (\n")
        assert rb_links.server_url(cwd=git_repo.work_dir) is None

    def test_git_config(self, git_repo: GitRepo, no_home_rc: Path) -> None:
        git_repo.git("config", "reviewboard.url", "https://git.example.com/")
        assert rb_links.server_url(cwd=git_repo.work_dir) == "https://git.example.com/"

    def test_none(self, git_repo: GitRepo, no_home_rc: Path) -> None:
        assert rb_links.server_url(cwd=git_repo.work_dir) is None


class TestLinks:
    def test_plain_without_server(self) -> None:
        assert Links().show("19986", 10) == "r/19986   "

    def test_url(self) -> None:
        assert Links("https://rb.example.com").url("7") == "https://rb.example.com/r/7/"
        assert Links("https://rb.example.com/").url("7") == "https://rb.example.com/r/7/"

    def test_hyperlink_pads_outside_the_link(self) -> None:
        links = Links("https://rb.example.com/", hyperlink=True)
        assert links.show("7", 6) == (
            "\x1b]8;;https://rb.example.com/r/7/\x1b\\r/7\x1b]8;;\x1b\\   "
        )

    def test_full_shows_the_url(self) -> None:
        links = Links("https://rb.example.com/", full=True)
        assert links.text("7") == "https://rb.example.com/r/7/"
        assert links.show("7") == "https://rb.example.com/r/7/"

    def test_full_without_server_falls_back(self) -> None:
        assert Links(None, full=True).show("7") == "r/7"


class TestLinksFor:
    def test_hyperlinks_on_a_terminal(
        self, git_repo: GitRepo, no_home_rc: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _rc(git_repo.work_dir, "https://rb.example.com/")
        monkeypatch.setenv("TERM", "xterm-256color")
        links = rb_links.links_for(tty=True, cwd=git_repo.work_dir)
        assert links == Links("https://rb.example.com/", hyperlink=True)

    def test_plain_when_piped(self, git_repo: GitRepo, no_home_rc: Path) -> None:
        _rc(git_repo.work_dir, "https://rb.example.com/")
        links = rb_links.links_for(tty=False, cwd=git_repo.work_dir)
        assert links == Links("https://rb.example.com/")

    def test_plain_on_a_dumb_terminal(
        self, git_repo: GitRepo, no_home_rc: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _rc(git_repo.work_dir, "https://rb.example.com/")
        monkeypatch.setenv("TERM", "dumb")
        assert not rb_links.links_for(tty=True, cwd=git_repo.work_dir).hyperlink

    def test_full_is_not_a_hyperlink(
        self, git_repo: GitRepo, no_home_rc: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _rc(git_repo.work_dir, "https://rb.example.com/")
        monkeypatch.setenv("TERM", "xterm-256color")
        links = rb_links.links_for(full=True, tty=True, cwd=git_repo.work_dir)
        assert links == Links("https://rb.example.com/", full=True)
