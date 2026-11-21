from datetime import timedelta

from outreach.pipeline import discover, parse_ts, post_approved

from .conftest import NOW, make_post
from .fakes import FakeDrafter, FakeThreads, crisis


def test_parse_ts_handles_threads_format():
    assert parse_ts("2026-11-19T16:00:00+0000") == NOW - timedelta(hours=2)
    assert parse_ts("") is None and parse_ts("garbage") is None


def test_discover_dedupes_filters_and_drafts(cfg, store):
    posts = [
        make_post("1", "alice"),
        make_post("2", "me"),                                   # our own post
        make_post("3", "bob", text="tired"),                    # too short
        make_post("4", "alice", text="Another post from alice about her newborn and no sleep."),
        make_post("5", "carol"),
    ]
    threads = FakeThreads({"postpartum": posts, "fourth trimester": [make_post("1", "alice")]})
    drafter = FakeDrafter({"5": crisis()})

    result = discover(cfg, threads, store, drafter, NOW)

    assert result.new == 3                                      # 1, 4, 5 (1 seen twice)
    assert result.drafted == 1                                  # alice once
    assert result.skipped["author_cooldown"] == 1               # alice's second post
    assert result.skipped["category:crisis"] == 1               # carol: never replied to
    assert [r.id for r in store.with_status("drafted")] == ["1"]
    assert sorted(drafter.calls) == ["1", "5"]

    # Running again drafts nothing new.
    again = discover(cfg, threads, store, drafter, NOW)
    assert again.new == 0 and again.drafted == 0


def test_discover_survives_search_errors_and_skips_stale(cfg, store):
    old = make_post("9", "dana", hours_ago=10)
    threads = FakeThreads({"postpartum": [old]})
    cfg_queries = cfg.search.keywords
    cfg.search.keywords.insert(0, "boom")
    try:
        result = discover(cfg, threads, store, FakeDrafter(), NOW + timedelta(hours=40))
    finally:
        cfg_queries.remove("boom")
    assert any("search broke" in e for e in result.errors)
    assert result.skipped["stale"] == 1


def _approve(store, post, text, link=False):
    store.add_post(post, "postpartum", NOW)
    store.approve(post.id, text, link, NOW)


def test_post_publishes_paces_and_caps(cfg, store):
    for i in range(12):
        _approve(store, make_post(str(i), f"user{i}"), f"Reply number {i}, you're doing so well.")
    threads = FakeThreads()
    clock = [NOW]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock[0] += timedelta(seconds=s)

    result = post_approved(cfg, threads, store, now_fn=lambda: clock[0], sleep=sleep, log=lambda m: None)

    assert result.posted == cfg.limits.max_posts_per_day == 10
    assert len(threads.replies) == 10
    assert sleeps == [cfg.limits.seconds_between_posts] * 9
    assert len(store.with_status("approved")) == 2

    later = post_approved(cfg, threads, store, now_fn=lambda: clock[0], sleep=sleep, log=lambda m: None)
    assert later.posted == 0 and "daily cap" in later.stopped_because


def test_post_dry_run_posts_nothing(cfg, store):
    _approve(store, make_post("1"), "You're not alone in this.")
    threads = FakeThreads()
    result = post_approved(cfg, threads, store, now_fn=lambda: NOW, sleep=lambda s: None, dry_run=True,
                           log=lambda m: None)
    assert result.posted == 1 and threads.replies == []
    assert len(store.with_status("approved")) == 1


def test_post_revalidates_and_handles_failures(cfg, store):
    _approve(store, make_post("1", "a"), "go to www.spam.com")
    _approve(store, make_post("2", "b"), "So much love to you.")
    _approve(store, make_post("3", "c", hours_ago=1), "Sending you strength.")
    threads = FakeThreads()
    threads.fail_reply_for = {"2"}
    result = post_approved(cfg, threads, store, now_fn=lambda: NOW, sleep=lambda s: None, log=lambda m: None)
    # Invalid text is set aside; a failed publish stops the run so nothing goes out of order.
    assert result.failed == 2 and result.posted == 0
    assert "publishing failed" in result.stopped_because
    assert [r.id for r in store.with_status("approved")] == ["3"]


def test_post_respects_author_cooldown_and_quota(cfg, store):
    _approve(store, make_post("1", "same"), "First reply.")
    _approve(store, make_post("2", "same"), "Second reply.")
    threads = FakeThreads()
    result = post_approved(cfg, threads, store, now_fn=lambda: NOW, sleep=lambda s: None, log=lambda m: None)
    assert result.posted == 1 and result.skipped["author_cooldown"] == 1

    _approve(store, make_post("3", "new"), "Hi.")
    threads.quota = (1000, 1000)
    blocked = post_approved(cfg, threads, store, now_fn=lambda: NOW, sleep=lambda s: None, log=lambda m: None)
    assert blocked.posted == 0 and "quota" in blocked.stopped_because
