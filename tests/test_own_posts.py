"""Option 1: new posts of your own, and replies to the comments under them."""

import sqlite3
from datetime import timedelta

import httpx

from outreach.cli import main
from outreach.drafter import build_compose_prompt, build_user_message
from outreach.pipeline import inbox, post_approved
from outreach.store import Store
from outreach.threads_api import Comment, ThreadsClient

from .conftest import NOW, make_post
from .fakes import FakeDrafter, FakeThreads, crisis, support


def comment(cid, user, text="This is exactly how I feel at 4 weeks.", replied_to="p1", hidden=False):
    base = make_post(cid, user, text=text)
    return Comment(**base.__dict__, replied_to_id=replied_to, hidden=hidden)


def test_api_reads_comments_and_publishes_posts():
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        path = request.url.path
        if path.endswith("/conversation"):
            return httpx.Response(200, json={"data": [
                {"id": "c1", "text": "hi", "username": "a", "replied_to": {"id": "p1"}, "hide_status": "NOT_HUSHED"},
                {"id": "c2", "text": "spam", "username": "b", "replied_to": {"id": "p1"}, "hide_status": "HIDDEN"},
            ]})
        if path.endswith("/me/threads") and request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "p1", "text": "my post"}]})
        if path.endswith("/me/threads") and request.method == "POST":
            assert "reply_to_id" not in dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, json={"id": "c9"})
        if path.endswith("/c9"):
            return httpx.Response(200, json={"status": "FINISHED"})
        if path.endswith("/threads_publish"):
            return httpx.Response(200, json={"id": "m9"})
        raise AssertionError(path)

    client = ThreadsClient("tok", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    assert [p.id for p in client.my_posts(limit=5)] == ["p1"]
    comments = client.conversation("p1")
    assert [(c.id, c.replied_to_id, c.hidden) for c in comments] == [("c1", "p1", False), ("c2", "p1", True)]
    assert client.publish("hello") == "m9"


def test_inbox_collects_only_comments_that_need_you(cfg, store):
    threads = FakeThreads(me="me")
    threads.posts = [make_post("p1", "me", text="What's one thing nobody told you about week two?")]
    threads.comments = {"p1": [
        comment("c1", "alice"),                              # new: reply
        comment("c2", "me", replied_to="p1"),                # yours
        comment("c3", "bob"),                                # already answered by you below
        comment("c4", "me", text="thank you!", replied_to="c3"),
        comment("c5", "carol", replied_to="c1"),             # carol talking to alice: not yours
        comment("c6", "dana", hidden=True),                  # hidden by you
        comment("c7", "erin", replied_to="c2"),              # replying to your reply: yours
        comment("c8", "fay", text="I don't want to be here anymore"),
    ]}
    drafter = FakeDrafter({"c8": crisis()})

    result = inbox(cfg, threads, store, drafter, NOW)

    assert result.new_comments == 3                          # c1, c7, c8
    assert result.drafted == 2
    assert result.needs_you == [("crisis", "fay", "https://www.threads.com/@fay/post/c8")]
    drafted = {r.id: r for r in store.with_status("drafted", kind="comment")}
    assert set(drafted) == {"c1", "c7"}
    assert drafted["c1"].context.startswith("What's one thing")
    assert inbox(cfg, threads, store, drafter, NOW).new_comments == 0   # idempotent


def test_inbox_without_drafter_only_collects(cfg, store):
    threads = FakeThreads(me="me")
    threads.posts = [make_post("p1", "me"), make_post("broken", "me")]
    threads.comments = {"p1": [comment("c1", "alice")]}
    result = inbox(cfg, threads, store, None, NOW, log=lambda m: None)
    assert result.new_comments == 1 and result.drafted == 0 and len(result.errors) == 1
    assert [r.id for r in store.with_status("new", kind="comment")] == ["c1"]


def test_posting_uses_separate_caps_for_posts_and_replies(cfg, store):
    for i in range(5):
        pid = store.add_new_post(f"Slow days still count, number {i}.", "", "permission", NOW)
        store.approve(pid, f"Slow days still count, number {i}.", False, NOW)
    for i in range(3):
        store.add_post(comment(f"c{i}", f"user{i}"), "comment", NOW, kind="comment", context="post")
        store.approve(f"c{i}", "Thank you for sharing this.", False, NOW)
    threads = FakeThreads()
    result = post_approved(cfg, threads, store, now_fn=lambda: NOW, sleep=lambda s: None, log=lambda m: None)

    assert len(threads.published) == cfg.limits.max_new_posts_per_day == 3
    assert len(threads.replies) == 3
    assert result.posted == 6 and "2 waiting on the daily cap for new posts" in result.stopped_because


def test_old_database_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE posts (id TEXT PRIMARY KEY, username TEXT, text TEXT, permalink TEXT,"
                " posted_at TEXT, query TEXT, discovered_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',"
                " skip_reason TEXT, category TEXT, rationale TEXT, draft_body TEXT, draft_link_line TEXT,"
                " final_text TEXT, includes_link INTEGER NOT NULL DEFAULT 0, reviewed_at TEXT, reply_id TEXT,"
                " reply_permalink TEXT, replied_at TEXT, error TEXT)")
    con.execute("INSERT INTO posts (id, discovered_at) VALUES ('1', '2026-01-01')")
    con.commit()
    con.close()
    store = Store(db)
    assert store.find("1").kind == "search"
    store.close()


def test_prompts():
    from outreach.config import SiteConfig, VoiceConfig

    prompt = build_compose_prompt(VoiceConfig(facts=["I made lettersfornewmom.com"]),
                                  SiteConfig(description="free letters", link_url="https://x.com"))
    assert "- I made lettersfornewmom.com" in prompt and "{" not in prompt
    msg = build_user_message(make_post("c1", "alice", text="love this"), context="my post text")
    assert msg.startswith("<your_post>\nmy post text") and "<comment>" in msg


def test_cli_draft_list_approve_skip(cfg, capsys):
    config = str(cfg.db_path.parent / "config.toml")
    main(["--config", config, "draft", "--new-post", "--theme", "question",
          "--body", "What surprised you most about the first month?",
          "--link-line", "I made a little site with a daily letter from another mom: [LINK]"])
    post_id = capsys.readouterr().out.split("draft ")[1].strip(" .\n")

    main(["--config", config, "list"])
    listed = capsys.readouterr().out
    assert "NEW POST" in listed and "lettersfornewmom.com" in listed and "1 drafted" in listed

    main(["--config", config, "approve", post_id[-4:]])
    assert "approved (with link)" in capsys.readouterr().out

    store = Store(cfg.db_path)
    store.add_post(comment("c42", "alice"), "comment", NOW, kind="comment", context="post")
    store.close()
    main(["--config", config, "draft", "c42", "--category", "venting", "--body", "That sounds exhausting."])
    assert "drafted" in capsys.readouterr().out
    main(["--config", config, "approve", "c42", "--text", "see www.other.com"])
    assert "not approved" in capsys.readouterr().out
    main(["--config", config, "skip", "c42"])
    assert "skipped" in capsys.readouterr().out
    main(["--config", config, "draft", "c42", "--category", "crisis", "--body", "x"])
    assert "skipped (category:crisis)" in capsys.readouterr().out
