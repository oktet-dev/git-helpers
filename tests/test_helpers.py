"""Tests for is_help, do_help, do_verbose utility functions."""

from tests.conftest import run_gitgo


class TestIsHelp:
    def test_dash_h_returns_zero(self) -> None:
        r = run_gitgo("is_help", "-h")
        assert r.returncode == 0

    def test_double_dash_help_returns_zero(self) -> None:
        r = run_gitgo("is_help", "--help")
        assert r.returncode == 0

    def test_random_arg_returns_one(self) -> None:
        r = run_gitgo("is_help", "foo")
        assert r.returncode == 1

    def test_no_args_returns_one(self) -> None:
        r = run_gitgo("is_help")
        assert r.returncode == 1

    def test_uppercase_H_returns_one(self) -> None:
        r = run_gitgo("is_help", "-H")
        assert r.returncode == 1

    def test_empty_string_returns_one(self) -> None:
        r = run_gitgo("is_help", "")
        assert r.returncode == 1


class TestDoHelp:
    def test_output_has_two_space_indent(self) -> None:
        r = run_gitgo("do_help", "some help text")
        assert r.stdout.startswith("  ")
        assert "some help text" in r.stdout


class TestDoVerbose:
    def test_executes_command(self) -> None:
        r = run_gitgo("do_verbose", "echo", "hello")
        # do_verbose uses set -x in a subshell, so the command trace
        # goes to stderr and the output goes to stdout
        assert "hello" in r.stdout
