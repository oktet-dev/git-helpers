"""ReviewBoard client for `gg comments`, over the RBTools Python library.

`RBToolsApi` implements `rb_model.RBApi` against a live server; `connect()`
sets it up from `.reviewboardrc` the way `rbt` does. The JSON translators
turn RB API payloads (the item objects of the JSON responses) into model
objects and need no server. Every RB call goes through `call_with_retry`,
which retries rate-limited calls and turns RBTools errors into `RBError`.

The client is shared by the worker threads of `gg comments`, so it runs
without RBTools' HTTP cache (an SQLite connection bound to one thread).
"""

from __future__ import annotations

import html
import os
import re
import sys
import time
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse

from rbtools.api.client import RBClient
from rbtools.api.errors import (
    HTTP_STATUS_CODES,
    APIError,
    CacheError,
    ServerInterfaceError,
)
from rbtools.config.config import RBToolsConfig
from rbtools.config.errors import ConfigError
from rbtools.config.loader import load_config

from gg.rb_model import (
    Comment,
    DiffRow,
    RBApi,
    RBError,
    Reply,
    ReplyComment,
    RequestInfo,
    Review,
)
from gg.rbt_retry import rate_limit_schedule

T = TypeVar("T")

_RATE_LIMIT_CODE = 114         # RB API error "API Rate Limit Exceeded"
_DOES_NOT_EXIST = 100          # RB API error "Does Not Exist"
_TOO_MANY_REQUESTS = 429
_PAGE_SIZE = 200               # RB's largest max-results

# Comment kind -> (the list's URL segment, a name for messages, the key of
# one comment in RB's answers).
_COMMENT_LISTS = {
    "diff": ("diff-comments", "diff comment", "diff_comment"),
    "general": ("general-comments", "general comment", "general_comment"),
    "attachment": ("file-attachment-comments", "file attachment comment",
                   "file_attachment_comment"),
}

_DIFF_LINK_RE = re.compile(r"/diffs/(\d+)/files/(\d+)/?$")

# RBTools failures, plus network errors RBTools lets through.
_CALL_ERRORS = (APIError, ServerInterfaceError, CacheError, OSError, HTTPException)


# --- JSON translators ------------------------------------------------------


def _author(data: dict) -> str:
    return data["links"]["user"]["title"]


def _last_id(href: str) -> int:
    """The id at the end of an RB API URL (`…/diff-comments/37441/` -> 37441)."""
    return int(urlparse(href).path.rstrip("/").rsplit("/", 1)[-1])


def _diff_link(link: dict | None) -> tuple[int | None, int | None]:
    """(diff revision, file diff id) from a `…/diffs/<rev>/files/<id>/` link."""
    if not link:
        return None, None
    m = _DIFF_LINK_RE.search(urlparse(link["href"]).path)
    if not m:
        raise RBError(f"unexpected file diff link: {link['href']}")
    return int(m.group(1)), int(m.group(2))


def comment_from_json(kind: str, data: dict, *, path: str | None = None,
                      attachment: str | None = None) -> Comment:
    """A reviewer's comment from its RB payload; `path`/`attachment` name its target."""
    links = data.get("links", {})
    revision, filediff_id = _diff_link(links.get("filediff"))
    interdiff_revision, interfilediff_id = _diff_link(links.get("interfilediff"))
    issue_opened = bool(data.get("issue_opened"))
    extra = data.get("extra_data") or {}
    return Comment(
        kind=kind,
        id=data["id"],
        author=_author(data),
        timestamp=data["timestamp"],
        text=data["text"],
        issue_opened=issue_opened,
        issue_status=(data.get("issue_status") or "") if issue_opened else "",
        severity=extra.get("severity"),
        path=path,
        filediff_id=filediff_id,
        interfilediff_id=interfilediff_id,
        diff_revision=revision,
        interdiff_revision=interdiff_revision,
        first_row=data.get("first_line"),
        num_rows=data.get("num_lines"),
        attachment=attachment,
    )


def reply_comment_from_json(kind: str, data: dict) -> ReplyComment:
    """A reply comment from its RB payload; it answers the comment `links.reply_to`."""
    return ReplyComment(
        kind=kind,
        reply_to=_last_id(data["links"]["reply_to"]["href"]),
        author=_author(data),
        timestamp=data["timestamp"],
        text=data["text"],
    )


def _line_no(value: Any) -> int | None:
    return None if value in ("", None) else int(value)


def diff_rows_from_json(data: dict) -> list[DiffRow]:
    """The rows of a file diff's diff data (`diff_data`), texts HTML-unescaped.

    Each chunk line is `[row, old_no, old_text, old_ranges, new_no, new_text,
    new_ranges, whitespace_only]`; a side a line is absent from has "" for
    its line number.
    """
    rows = []
    for chunk in data.get("chunks", []):
        for line in chunk["lines"]:
            rows.append(DiffRow(
                row=int(line[0]),
                old_line=_line_no(line[1]),
                old_text=html.unescape(line[2]),
                new_line=_line_no(line[4]),
                new_text=html.unescape(line[5]),
            ))
    return rows


# --- Retry -----------------------------------------------------------------


def _is_rate_limit(e: APIError) -> bool:
    return e.error_code == _RATE_LIMIT_CODE or e.http_status == _TOO_MANY_REQUESTS


def _describe(e: Exception) -> str:
    """An error's message; an HTTP error without an RB API error (an HTML page) by its status."""
    if isinstance(e, APIError) and e.error_code is None and e.http_status is not None:
        return f"HTTP {e.http_status} {HTTP_STATUS_CODES.get(e.http_status, '')}".rstrip()
    return str(e)


def call_with_retry(fn: Callable[[], T], *, what: str = "ReviewBoard call",
                    sleep: Callable[[float], None] = time.sleep) -> T:
    """Return `fn()`, retrying it while RB rate-limits it.

    A rate-limited call is retried after each delay of
    `rbt_retry.rate_limit_schedule()`. Any other RBTools or network error,
    or a rate limit after the last retry, raises `RBError("<what> failed:
    <message>")`. An `RBError` raised by `fn` passes through.
    """
    delays = rate_limit_schedule()
    while True:
        try:
            return fn()
        except APIError as e:
            if _is_rate_limit(e) and delays:
                delay = delays.pop(0)
                print(f"[gg] ReviewBoard rate limit; retrying in {delay}s",
                      file=sys.stderr, flush=True)
                sleep(delay)
                continue
            raise RBError(f"{what} failed: {_describe(e)}") from e
        except _CALL_ERRORS as e:
            raise RBError(f"{what} failed: {_describe(e)}") from e


# --- Client ----------------------------------------------------------------


def _item(resource: Any, key: str | None = None) -> dict:
    """The JSON object of an item resource, without the response envelope.

    RBTools finds the object's key only in a response with nothing else in
    it; `key` names the object in one with more (an issue status change
    also returns `last_activity_time`).
    """
    token = resource._token or key
    return resource.rsp[token] if token else resource.rsp


def _load_config(cwd: Path) -> RBToolsConfig:
    """`rbt`'s configuration as seen from `cwd` (RBTools reads the process cwd)."""
    old = os.getcwd()
    os.chdir(cwd)
    try:
        return load_config()
    except ConfigError as e:
        raise RBError(f"reading .reviewboardrc failed: {e}") from e
    finally:
        os.chdir(old)


def _serialize_cookie_saves(client: RBClient) -> None:
    """Keep RBTools' cookie file intact when requests run in several threads.

    RBTools rewrites the cookie file (`rbt login`'s session) after every
    request. Saving under the jar's own lock keeps two rewrites from
    interleaving, and a rewrite from racing a response that sets a cookie.
    """
    jar = client._transport.server.cookie_jar
    save = jar.save

    def locked_save(*args: Any, **kwargs: Any) -> None:
        with jar._cookies_lock:
            save(*args, **kwargs)

    jar.save = locked_save


def connect(*, cwd: Path) -> RBApi:
    """An `RBToolsApi` for the server and login `rbt` would use in `cwd`.

    Authentication as in `rbt`: USERNAME/PASSWORD or API_TOKEN from
    `.reviewboardrc`, else the session in RBTools' cookie file (`rbt login`).
    """
    config = _load_config(cwd)
    url = config.REVIEWBOARD_URL
    if not url:
        raise RBError("no REVIEWBOARD_URL in .reviewboardrc")
    if not urlparse(url).scheme:
        url = f"http://{url}"

    def make() -> RBClient:
        client = RBClient(
            url,
            username=config.USERNAME,
            password=config.PASSWORD,
            api_token=config.API_TOKEN,
            verify_ssl=not config.DISABLE_SSL_VERIFICATION,
            ca_certs=config.CA_CERTS,
            client_key=config.CLIENT_KEY,
            client_cert=config.CLIENT_CERT,
            allow_caching=False,
            config=config,
        )
        client.get_root()          # checks the server; an API token logs in here
        return client

    client = call_with_retry(make, what=f"connecting to {url}")
    _serialize_cookie_saves(client)
    return RBToolsApi(client)


class RBToolsApi:
    """`RBApi` over an RBTools client; safe to share between threads.

    Resources are addressed by their RB API paths. File diffs, attachment
    names and diff rows are cached for the life of the object.
    """

    def __init__(self, client: RBClient) -> None:
        self._client = client
        self._user: str | None = None
        self._filediffs: dict[tuple[str, int, int], Any] = {}
        self._attachments: dict[str, str] = {}
        self._rows: dict[tuple[str, int, int], list[DiffRow]] = {}

    # Paths and plumbing.

    @staticmethod
    def _review_path(rid: str, review_id: int) -> str:
        return f"review-requests/{rid}/reviews/{review_id}/"

    @classmethod
    def _reply_path(cls, rid: str, review_id: int, reply_id: int) -> str:
        return f"{cls._review_path(rid, review_id)}replies/{reply_id}/"

    def _get(self, path: str, what: str) -> Any:
        return call_with_retry(lambda: self._client.get_path(path), what=what)

    def _list(self, path: str, what: str) -> list[dict]:
        """The JSON objects of every page of the list at `path`."""
        def fetch() -> list[dict]:
            first = self._client.get_path(path, max_results=_PAGE_SIZE)
            return [item.rsp for item in first.all_items]
        return call_with_retry(fetch, what=what)

    def _change(self, path: str, action: str, what: str, **fields: Any) -> Any:
        """Fetch the resource at `path` and run its `create`/`update`/`delete`."""
        def call() -> Any:
            resource = self._client.get_path(path)
            if action not in resource.links:
                raise RBError(f"{what} failed: ReviewBoard does not allow it")
            return getattr(resource, action)(**fields)
        return call_with_retry(call, what=what)

    # Reading.

    def user(self) -> str:
        if self._user is None:
            session = _item(self._get("session/", "reading the ReviewBoard session"))
            user = session.get("links", {}).get("user")
            if not session.get("authenticated") or not user:
                raise RBError("not logged in to ReviewBoard: run `rbt login` "
                              "or set API_TOKEN in .reviewboardrc")
            self._user = user["title"]
        return self._user

    def request(self, rid: str) -> RequestInfo:
        data = _item(self._get(f"review-requests/{rid}/", "reading the review request"))
        latest = data["links"].get("latest_diff")
        return RequestInfo(
            id=rid,
            summary=data["summary"],
            latest_diff=_last_id(latest["href"]) if latest else None,
        )

    def reviews(self, rid: str) -> list[Review]:
        reviews = self._list(f"review-requests/{rid}/reviews/", "reading reviews")
        return [self._review(rid, data) for data in reviews]

    def _review(self, rid: str, data: dict) -> Review:
        path = self._review_path(rid, data["id"])
        comments = []
        for kind, (segment, name, _) in _COMMENT_LISTS.items():
            for c in self._list(path + segment + "/",
                                f"reading {name}s of review {data['id']}"):
                comments.append(self._comment(rid, kind, c))
        replies = self._list(path + "replies/", f"reading replies to review {data['id']}")
        return Review(
            id=data["id"],
            author=_author(data),
            timestamp=data["timestamp"],
            public=bool(data["public"]),
            ship_it=bool(data["ship_it"]),
            body_top=data.get("body_top") or "",
            comments=comments,
            replies=[self._reply(rid, data["id"], r) for r in replies],
            body_bottom=data.get("body_bottom") or "",
        )

    def _reply(self, rid: str, review_id: int, data: dict) -> Reply:
        path = self._reply_path(rid, review_id, data["id"])
        comments = []
        for kind, (segment, name, _) in _COMMENT_LISTS.items():
            for c in self._list(path + segment + "/",
                                f"reading {name}s of reply {data['id']}"):
                comments.append(reply_comment_from_json(kind, c))
        return Reply(
            id=data["id"],
            author=_author(data),
            timestamp=data["timestamp"],
            public=bool(data["public"]),
            body_top=data.get("body_top") or "",
            comments=comments,
            body_bottom=data.get("body_bottom") or "",
        )

    def _comment(self, rid: str, kind: str, data: dict) -> Comment:
        links = data.get("links", {})
        path = attachment = None
        if kind == "diff":
            revision, filediff_id = _diff_link(links.get("filediff"))
            if filediff_id is not None:
                path = _item(self._filediff(rid, revision, filediff_id))["dest_file"]
        elif kind == "attachment" and links.get("file_attachment"):
            attachment = self._attachment_name(links["file_attachment"]["href"])
        return comment_from_json(kind, data, path=path, attachment=attachment)

    def _filediff(self, rid: str, revision: int, filediff_id: int) -> Any:
        key = (rid, revision, filediff_id)
        if key not in self._filediffs:
            self._filediffs[key] = self._get(
                f"review-requests/{rid}/diffs/{revision}/files/{filediff_id}/",
                f"reading file diff {filediff_id}")
        return self._filediffs[key]

    def _attachment_name(self, href: str) -> str:
        if href not in self._attachments:
            resource = call_with_retry(
                lambda: self._client.get_url(href),
                what=f"reading file attachment {_last_id(href)}")
            self._attachments[href] = _item(resource)["filename"]
        return self._attachments[href]

    def diff_rows(self, rid: str, comment: Comment) -> list[DiffRow]:
        if comment.filediff_id is None or comment.diff_revision is None:
            raise RBError(f"comment {comment.id} is not on a file diff")
        if comment.interfilediff_id is not None:
            # An interdiff comment's rows are rows of the interdiff view; RB's
            # API serves the diff data of single file diffs only.
            raise RBError(f"ReviewBoard's API has no interdiff rows "
                          f"(comment {comment.id})")
        key = (rid, comment.diff_revision, comment.filediff_id)
        if key not in self._rows:
            filediff = self._filediff(rid, comment.diff_revision, comment.filediff_id)
            data = call_with_retry(
                filediff.get_diff_data,
                what=f"reading the diff of file diff {comment.filediff_id}")
            self._rows[key] = diff_rows_from_json(_item(data))
        return self._rows[key]

    def draft_reply(self, rid: str, review_id: int) -> Reply | None:
        path = f"{self._review_path(rid, review_id)}replies/draft/"

        def fetch() -> dict | None:
            try:
                return _item(self._client.get_path(path))   # redirects to the draft
            except APIError as e:
                if e.error_code == _DOES_NOT_EXIST:          # no draft
                    return None
                raise

        data = call_with_retry(fetch, what=f"reading your reply draft to review {review_id}")
        return None if data is None else self._reply(rid, review_id, data)

    # Writing.

    def create_reply(self, rid: str, review_id: int) -> int:
        reply = self._change(f"{self._review_path(rid, review_id)}replies/", "create",
                             f"creating a reply to review {review_id}")
        return _item(reply)["id"]

    def add_reply_comment(self, rid: str, review_id: int, reply_id: int,
                          comment: Comment, text: str) -> None:
        segment, name, _ = _COMMENT_LISTS[comment.kind]
        self._change(f"{self._reply_path(rid, review_id, reply_id)}{segment}/", "create",
                     f"replying to {name} {comment.id}",
                     reply_to_id=comment.id, text=text, text_type="markdown")

    def set_reply_top(self, rid: str, review_id: int, reply_id: int, text: str) -> None:
        self._change(self._reply_path(rid, review_id, reply_id), "update",
                     f"replying to the top text of review {review_id}",
                     body_top=text, body_top_text_type="markdown")

    def publish_reply(self, rid: str, review_id: int, reply_id: int) -> None:
        self._change(self._reply_path(rid, review_id, reply_id), "update",
                     f"publishing reply {reply_id}", public=True)

    def delete_reply(self, rid: str, review_id: int, reply_id: int) -> None:
        self._change(self._reply_path(rid, review_id, reply_id), "delete",
                     f"deleting reply {reply_id}")

    def set_issue_status(self, rid: str, review_id: int, comment: Comment,
                         status: str) -> str:
        segment, name, key = _COMMENT_LISTS[comment.kind]
        updated = self._change(
            f"{self._review_path(rid, review_id)}{segment}/{comment.id}/", "update",
            f"setting the issue status of {name} {comment.id}", issue_status=status)
        return _item(updated, key)["issue_status"]
