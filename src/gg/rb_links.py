"""Links to review requests: r/N as a terminal hyperlink, or the full URL."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_RC = ".reviewboardrc"


def _rc_url(path: Path) -> str | None:
    """REVIEWBOARD_URL set in a .reviewboardrc (Python syntax), if any."""
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError, ValueError):
        return None
    url = None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "REVIEWBOARD_URL" for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            url = node.value.value
    return url


def server_url(*, cwd: Path | None = None) -> str | None:
    """The ReviewBoard URL rbt would use, or None.

    Looks where rbtools does, first hit wins: the directories in
    $RBTOOLS_CONFIG_PATH, cwd and its parents, the home directory; then
    git's reviewboard.url.
    """
    start = (cwd or Path.cwd()).resolve()
    dirs = [Path(d) for d in os.environ.get("RBTOOLS_CONFIG_PATH", "").split(os.pathsep) if d]
    dirs += [start, *start.parents, Path.home()]
    for d in dirs:
        url = _rc_url(d / _RC)
        if url:
            return url
    r = subprocess.run(
        ["git", "config", "--get", "reviewboard.url"],
        cwd=start, capture_output=True, text=True,
    )
    return r.stdout.strip() or None


@dataclass(frozen=True)
class Links:
    """How a review is shown: r/N, r/N as an OSC 8 hyperlink, or its URL."""

    server: str | None = None
    full: bool = False
    hyperlink: bool = False

    def url(self, review_id: str) -> str | None:
        if not self.server:
            return None
        return f"{self.server.rstrip('/')}/r/{review_id}/"

    def text(self, review_id: str) -> str:
        """The visible text for a review."""
        url = self.url(review_id)
        return url if self.full and url else f"r/{review_id}"

    def show(self, review_id: str, width: int = 0) -> str:
        """text() padded to width; the padding stays outside a hyperlink."""
        text = self.text(review_id)
        pad = " " * (width - len(text))
        url = self.url(review_id)
        if self.hyperlink and url:
            return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\{pad}"
        return text + pad


def links_for(
    *, full: bool = False, tty: bool | None = None, cwd: Path | None = None,
) -> Links:
    """Links for output to stdout: hyperlinks on a terminal, URLs with full."""
    if tty is None:
        tty = sys.stdout.isatty()
    hyperlink = tty and not full and os.environ.get("TERM") != "dumb"
    return Links(server_url(cwd=cwd), full=full, hyperlink=hyperlink)
