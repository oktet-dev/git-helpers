"""Tests for rb_client.py: RB JSON translators, the rate-limit retry, and
RBToolsApi driving real RBTools resources over a fake transport."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from rbtools.api.client import RBClient
from rbtools.api.errors import APIError, ServerInterfaceError
from rbtools.api.factory import create_resource
from rbtools.api.transport.sync import SyncTransport

from gg.rb_client import (
    RBToolsApi,
    call_with_retry,
    comment_from_json,
    diff_rows_from_json,
    reply_comment_from_json,
)
from gg.rb_model import Comment, DiffRow, RBError, Reply, ReplyComment, RequestInfo, Review
from gg.rbt_retry import rate_limit_schedule

API = "https://rb.example.com/api"
TS = "2026-10-16T08:47:13Z"


def link(path: str, title: str | None = None) -> dict:
    out = {"href": f"{API}{path}", "method": "GET"}
    if title is not None:
        out["title"] = title
    return out


def diff_comment_json(**over) -> dict:
    data = {
        "id": 37441,
        "text": "Why does it matter?",
        "text_type": "plain",
        "timestamp": TS,
        "public": True,
        "first_line": 141,
        "num_lines": 3,
        "issue_opened": True,
        "issue_status": "open",
        "extra_data": {"require_verification": False, "severity": "info"},
        "interfilediff": None,
        "links": {
            "self": link("/review-requests/19954/reviews/36005/diff-comments/37441/"),
            "user": link("/users/ark-slav/", "ark-slav"),
            "filediff": link("/review-requests/19954/diffs/1/files/234050/",
                             "net-drv-ts/dcb/dcb_ets.c"),
        },
    }
    data.update(over)
    return data


def test_diff_comment_from_json():
    c = comment_from_json("diff", diff_comment_json(), path="net-drv-ts/dcb/dcb_ets.c")
    assert c == Comment("diff", 37441, "ark-slav", TS, "Why does it matter?", True, "open",
                        "info", path="net-drv-ts/dcb/dcb_ets.c", filediff_id=234050,
                        diff_revision=1, first_row=141, num_rows=3)


def test_comment_without_issue_has_empty_status():
    data = {
        "id": 812,
        "text": "Nice cleanup.",
        "timestamp": TS,
        "issue_opened": False,
        "issue_status": "open",
        "extra_data": {},
        "links": {"user": link("/users/ark-roma/", "ark-roma")},
    }
    c = comment_from_json("general", data)
    assert c == Comment("general", 812, "ark-roma", TS, "Nice cleanup.", False, "")


def test_interdiff_comment_from_json():
    data = diff_comment_json()
    data["links"]["interfilediff"] = link("/review-requests/19954/diffs/2/files/235111/")
    c = comment_from_json("diff", data, path="net-drv-ts/dcb/dcb_ets.c")
    assert (c.diff_revision, c.filediff_id) == (1, 234050)
    assert (c.interdiff_revision, c.interfilediff_id) == (2, 235111)


def test_attachment_comment_from_json():
    data = {
        "id": 95,
        "text": "Wrong label.",
        "timestamp": TS,
        "issue_opened": True,
        "issue_status": "dropped",
        "extra_data": {},
        "links": {
            "user": link("/users/ark-slav/", "ark-slav"),
            "file_attachment": link("/review-requests/19954/file-attachments/77/"),
        },
    }
    c = comment_from_json("attachment", data, attachment="topology.png")
    assert c == Comment("attachment", 95, "ark-slav", TS, "Wrong label.", True, "dropped",
                        attachment="topology.png")


def test_reply_comment_from_json():
    data = {
        "id": 37502,
        "text": "Fewer than 3 TCs is legal.",
        "timestamp": "2026-10-17T10:00:00Z",
        "links": {
            "user": link("/users/kostik/", "kostik"),
            "reply_to": link("/review-requests/19954/reviews/36005/diff-comments/37441/"),
        },
    }
    rc = reply_comment_from_json("diff", data)
    assert rc == ReplyComment("diff", 37441, "kostik", "2026-10-17T10:00:00Z",
                              "Fewer than 3 TCs is legal.")


def test_diff_rows_from_json():
    data = {
        "binary": False,
        "chunks": [
            {"change": "equal", "lines": [
                [140, 140, "", [], 140, "", [], False],
            ]},
            {"change": "insert", "lines": [
                [141, "", "", [], 141, "    if (current_ets.ets_cap &lt; 3)", [], False],
                [142, "", "", [], 142,
                 "        WARN_VERDICT(&quot;IUT supports fewer than 3 TCs&quot;);", [], False],
            ]},
            {"change": "delete", "lines": [
                [143, 141, "    return &amp;x;", [], "", "", [], False],
            ]},
        ],
    }
    rows = diff_rows_from_json(data)
    assert [r.row for r in rows] == [140, 141, 142, 143]
    assert rows[1] == DiffRow(141, None, "", 141, "    if (current_ets.ets_cap < 3)")
    assert rows[2].new_text == '        WARN_VERDICT("IUT supports fewer than 3 TCs");'
    assert rows[3] == DiffRow(143, 141, "    return &x;", None, "")


def test_retry_on_rate_limit(monkeypatch, capsys):
    monkeypatch.setenv("GG_RBT_RATE_LIMIT_INITIAL_DELAY", "0")
    calls = []
    sleeps = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise APIError(429, 114, None, "Rate limit exceeded")
        return 5

    assert call_with_retry(fn, sleep=sleeps.append) == 5
    assert sleeps == [rate_limit_schedule()[0]]
    assert "[gg] ReviewBoard rate limit; retrying in 0s" in capsys.readouterr().err


def test_http_429_is_a_rate_limit(monkeypatch):
    monkeypatch.setenv("GG_RBT_RATE_LIMIT_INITIAL_DELAY", "0")
    results = iter([APIError(429, None, None, "Too Many Requests"), 7])

    def fn():
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    assert call_with_retry(fn, sleep=lambda s: None) == 7


def test_rate_limit_retries_run_out(monkeypatch):
    monkeypatch.setenv("GG_RBT_RATE_LIMIT_INITIAL_DELAY", "0")
    monkeypatch.setenv("GG_RBT_RATE_LIMIT_RETRIES", "2")
    sleeps = []

    def fn():
        raise APIError(429, 114, None, "Rate limit exceeded")

    with pytest.raises(RBError, match=r"^reading reviews failed: Rate limit exceeded"):
        call_with_retry(fn, what="reading reviews", sleep=sleeps.append)
    assert sleeps == [0, 0]


def test_other_error_becomes_rb_error():
    sleeps = []

    def not_found():
        raise APIError(404, 100, None, "Object does not exist")

    with pytest.raises(RBError, match=r"^reading reviews failed: Object does not exist"):
        call_with_retry(not_found, what="reading reviews", sleep=sleeps.append)

    def unreachable():
        raise ServerInterfaceError("Connection refused")

    with pytest.raises(RBError, match=r"^publishing reply 900 failed: Connection refused"):
        call_with_retry(unreachable, what="publishing reply 900", sleep=sleeps.append)
    assert sleeps == []


def test_non_api_http_error_is_short():
    def fn():
        raise APIError(404, None, None, "<!DOCTYPE html><html>...</html>")

    with pytest.raises(RBError) as e:
        call_with_retry(fn, what="reading reviews")
    assert str(e.value) == "reading reviews failed: HTTP 404 Not Found"


# --- RBToolsApi over a fake transport ----------------------------------------

SERVER = "https://rb.example.com/"
JSON = "application/json"
FILE_MIME = "application/vnd.reviewboard.org.file+json"   # a FileDiffItemResource
DIFF_DATA = "application/vnd.reviewboard.org.diff.data+json"
R = "review-requests/19954/"
REVIEW = R + "reviews/36005/"
REPLY = REVIEW + "replies/900/"
DRAFT = REVIEW + "replies/901/"
TS2 = "2026-10-17T10:00:00Z"


class FakeTransport(SyncTransport):
    """Answers RBTools requests from canned RB payloads and records them.

    Routes map (kind, API path) to (payload, mime type); kind is the HTTP
    method, or "diff-data" for a GET asking for a file diff's diff data.
    """

    def __init__(self, url: str, *, routes: dict) -> None:
        self.url = url
        self.server = SimpleNamespace(url=url + "api/")
        self.routes = routes
        self.requests: list[tuple[str, str, dict]] = []

    def _execute_request(self, request):
        path = urlparse(request.url).path.removeprefix("/api/")
        kind = request.method
        if request.headers.get("Accept") == DIFF_DATA:
            kind = "diff-data"
        fields = {k.decode(): v.decode() for k, v in request._fields.items()}
        self.requests.append((kind, path, fields))
        if kind == "DELETE":
            return None
        if (kind, path) not in self.routes:
            raise APIError(404, 100, None, "Object does not exist")
        payload, mime = self.routes[(kind, path)]
        return create_resource(transport=self, payload=copy.deepcopy(payload),
                               url=request.url, mime_type=mime)


def fake_api(routes: dict) -> tuple[RBToolsApi, FakeTransport]:
    client = RBClient(SERVER, transport_cls=FakeTransport, routes=routes)
    return RBToolsApi(client), client._transport


def item(key: str, data: dict, mime: str = JSON) -> tuple[dict, str]:
    return {"stat": "ok", key: data}, mime


def page(key: str, items: list, *, next: str | None = None,
         create: str | None = None) -> tuple[dict, str]:
    links = {"self": link("/" + key + "/")}
    if next:
        links["next"] = link("/" + next)
    if create:
        links["create"] = {"href": f"{API}/{create}", "method": "POST"}
    return {"stat": "ok", "total_results": len(items), "links": links, key: items}, JSON


def can(path: str, *actions: str) -> dict:
    """The links RB gives an object at `path` the user may change."""
    methods = {"update": "PUT", "delete": "DELETE"}
    return {a: {"href": f"{API}/{path}", "method": methods[a]} for a in actions}


def reply_json(id: int, *, public: bool, body_top: str = "", **links) -> dict:
    return {"id": id, "timestamp": TS2, "public": public, "body_top": body_top,
            "links": {"user": link("/users/kostik/", "kostik"), **links}}


def reply_comment_json(id: int, reply_to: str, text: str) -> dict:
    return {"id": id, "text": text, "timestamp": TS2,
            "links": {"user": link("/users/kostik/", "kostik"),
                      "reply_to": link("/" + reply_to)}}


def no_comments(path: str) -> dict:
    return {("GET", path + "diff-comments/"): page("diff_comments", []),
            ("GET", path + "general-comments/"): page("general_comments", []),
            ("GET", path + "file-attachment-comments/"):
                page("file_attachment_comments", [])}


def general_json(id: int, text: str) -> dict:
    return {"id": id, "text": text, "timestamp": TS, "issue_opened": False,
            "issue_status": "", "extra_data": {},
            "links": {"user": link("/users/ark-slav/", "ark-slav")}}


def read_routes() -> dict:
    attachment_comment = {
        "id": 95, "text": "Wrong label.", "timestamp": TS, "issue_opened": True,
        "issue_status": "open", "extra_data": {},
        "links": {"user": link("/users/ark-slav/", "ark-slav"),
                  "file_attachment": link("/" + R + "file-attachments/77/")},
    }
    return {
        ("GET", R): item("review_request", {
            "id": 19954, "summary": "[12/14]: dcb/dcb_ets: test valid ETS configurations",
            "links": {"latest_diff": link("/" + R + "diffs/2/")}}),
        ("GET", R + "reviews/"): page("reviews", [{
            "id": 36005, "timestamp": TS, "public": True, "ship_it": True,
            "body_top": "Ship It!", "links": {"user": link("/users/ark-slav/", "ark-slav")},
        }]),
        ("GET", REVIEW + "diff-comments/"): page("diff_comments", [
            diff_comment_json(),
            diff_comment_json(id=37442, text="Typo.", first_line=150, num_lines=1),
        ]),
        ("GET", REVIEW + "general-comments/"): page(
            "general_comments", [general_json(812, "Nice.")], next=REVIEW + "general-page2/"),
        ("GET", REVIEW + "general-page2/"): page("general_comments", [general_json(813, "And?")]),
        ("GET", REVIEW + "file-attachment-comments/"):
            page("file_attachment_comments", [attachment_comment]),
        ("GET", REVIEW + "replies/"): page("replies", [reply_json(900, public=True)]),
        ("GET", REPLY + "diff-comments/"): page("diff_comments", [
            reply_comment_json(950, REVIEW + "diff-comments/37441/", "Because.")]),
        ("GET", REPLY + "general-comments/"): page("general_comments", []),
        ("GET", REPLY + "file-attachment-comments/"): page("file_attachment_comments", []),
        ("GET", R + "diffs/1/files/234050/"): item(
            "file", {"id": 234050, "dest_file": "net-drv-ts/dcb/dcb_ets.c", "links": {}},
            FILE_MIME),
        ("diff-data", R + "diffs/1/files/234050/"): item("diff_data", {"chunks": [
            {"lines": [[141, "", "", [], 141, "    if (x &lt; 3)", [], False]]}]}),
        ("GET", R + "file-attachments/77/"): item(
            "file_attachment", {"id": 77, "filename": "topology.png"}),
    }


def test_request_info():
    api, _ = fake_api(read_routes())
    assert api.request("19954") == RequestInfo(
        "19954", "[12/14]: dcb/dcb_ets: test valid ETS configurations", 2)


def test_reviews_with_comments_replies_and_targets():
    api, rb = fake_api(read_routes())
    path = "net-drv-ts/dcb/dcb_ets.c"
    assert api.reviews("19954") == [Review(
        id=36005, author="ark-slav", timestamp=TS, public=True, ship_it=True,
        body_top="Ship It!",
        comments=[
            Comment("diff", 37441, "ark-slav", TS, "Why does it matter?", True, "open",
                    "info", path=path, filediff_id=234050, diff_revision=1,
                    first_row=141, num_rows=3),
            Comment("diff", 37442, "ark-slav", TS, "Typo.", True, "open", "info",
                    path=path, filediff_id=234050, diff_revision=1,
                    first_row=150, num_rows=1),
            Comment("general", 812, "ark-slav", TS, "Nice.", False, ""),
            Comment("general", 813, "ark-slav", TS, "And?", False, ""),
            Comment("attachment", 95, "ark-slav", TS, "Wrong label.", True, "open",
                    attachment="topology.png"),
        ],
        replies=[Reply(900, "kostik", TS2, True, "", [
            ReplyComment("diff", 37441, "kostik", TS2, "Because.")])],
    )]
    paths = [p for _, p, _ in rb.requests]
    assert paths.count(R + "diffs/1/files/234050/") == 1      # cached per file diff


def test_diff_rows_fetched_once_per_file_diff():
    api, rb = fake_api(read_routes())
    c = comment_from_json("diff", diff_comment_json(), path="net-drv-ts/dcb/dcb_ets.c")
    assert api.diff_rows("19954", c) == [DiffRow(141, None, "", 141, "    if (x < 3)")]
    api.diff_rows("19954", c)
    assert [k for k, _, _ in rb.requests].count("diff-data") == 1


def test_interdiff_comment_has_no_rows():
    api, rb = fake_api(read_routes())
    data = diff_comment_json()
    data["links"]["interfilediff"] = link("/" + R + "diffs/2/files/235111/")
    with pytest.raises(RBError, match="interdiff"):
        api.diff_rows("19954", comment_from_json("diff", data, path="x.c"))
    assert rb.requests == []


def test_draft_reply():
    api, _ = fake_api(read_routes())
    assert api.draft_reply("19954", 36005) is None

    routes = read_routes()
    routes[("GET", REVIEW + "replies/draft/")] = item(
        "reply", reply_json(901, public=False, body_top="Thanks"))
    routes.update(no_comments(DRAFT))
    routes[("GET", DRAFT + "general-comments/")] = page("general_comments", [
        reply_comment_json(951, REVIEW + "general-comments/812/", "Sure.")])
    api, _ = fake_api(routes)
    assert api.draft_reply("19954", 36005) == Reply(901, "kostik", TS2, False, "Thanks", [
        ReplyComment("general", 812, "kostik", TS2, "Sure.")])


def write_routes() -> dict:
    reply = item("reply", reply_json(901, public=False, **can(DRAFT, "update", "delete")))
    comment = REVIEW + "diff-comments/37441/"
    return {
        ("GET", REVIEW + "replies/"): page("replies", [], create=REVIEW + "replies/"),
        ("POST", REVIEW + "replies/"): reply,
        ("GET", DRAFT): reply,
        ("PUT", DRAFT): reply,
        ("GET", DRAFT + "diff-comments/"):
            page("diff_comments", [], create=DRAFT + "diff-comments/"),
        ("POST", DRAFT + "diff-comments/"): item("diff_comment", {"id": 960}),
        ("GET", DRAFT + "general-comments/"):
            page("general_comments", [], create=DRAFT + "general-comments/"),
        ("POST", DRAFT + "general-comments/"): item("general_comment", {"id": 961}),
        ("GET", comment): item("diff_comment", {
            **diff_comment_json(),
            "links": {**diff_comment_json()["links"], **can(comment, "update")}}),
        ("PUT", comment):
            item("diff_comment", diff_comment_json(issue_status="verifying-resolved")),
    }


def test_reply_writes():
    api, rb = fake_api(write_routes())
    diff = comment_from_json("diff", diff_comment_json(), path="x.c")
    general = comment_from_json("general", general_json(812, "Nice."))
    text = "Fixed:\n\n```c\nif (x)\n```"

    assert api.create_reply("19954", 36005) == 901
    api.add_reply_comment("19954", 36005, 901, diff, text)
    api.add_reply_comment("19954", 36005, 901, general, "Thanks.")
    api.set_reply_top("19954", 36005, 901, "Thanks!")
    api.publish_reply("19954", 36005, 901)
    assert api.set_issue_status("19954", 36005, diff, "resolved") == "verifying-resolved"
    api.delete_reply("19954", 36005, 901)

    assert [r for r in rb.requests if r[0] != "GET"] == [
        ("POST", REVIEW + "replies/", {}),
        ("POST", DRAFT + "diff-comments/",
         {"reply_to_id": "37441", "text": text, "text_type": "markdown"}),
        ("POST", DRAFT + "general-comments/",
         {"reply_to_id": "812", "text": "Thanks.", "text_type": "markdown"}),
        ("PUT", DRAFT, {"body_top": "Thanks!", "body_top_text_type": "markdown"}),
        ("PUT", DRAFT, {"public": "True"}),
        ("PUT", REVIEW + "diff-comments/37441/", {"issue_status": "resolved"}),
        ("DELETE", DRAFT, {}),
    ]


def test_write_refused_without_link():
    routes = write_routes()
    routes[("GET", REVIEW + "diff-comments/37441/")] = item("diff_comment", diff_comment_json())
    api, rb = fake_api(routes)
    diff = comment_from_json("diff", diff_comment_json(), path="x.c")
    with pytest.raises(RBError, match=r"^setting the issue status of diff comment 37441 "
                                      r"failed: ReviewBoard does not allow it"):
        api.set_issue_status("19954", 36005, diff, "resolved")
    assert [r[0] for r in rb.requests] == ["GET"]
