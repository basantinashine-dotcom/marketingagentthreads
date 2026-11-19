"""outreach: find postpartum conversations on Threads, draft replies, post what you approve.

    outreach check      verify your token, quota, and whether search sees public posts yet
    outreach discover   search Threads and draft replies with Claude
    outreach review     approve, edit or skip each draft (nothing posts without this)
    outreach post       publish approved replies, paced and capped
    outreach stats      what's been found, skipped and posted
    outreach purge      wipe post text older than the retention window
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime, timedelta

from .config import Config, ConfigError, load_config
from .store import Store, to_json


def _now() -> datetime:
    return datetime.now(UTC)


def _threads_client():
    from .threads_api import ThreadsClient

    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not token:
        sys.exit("Set THREADS_ACCESS_TOKEN (see .env.example).")
    return ThreadsClient(token)


def cmd_check(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    threads = _threads_client()
    user_id, username = threads.me()
    print(f"Token OK: @{username} (id {user_id})")
    used, total = threads.reply_quota()
    print(f"Meta reply quota: {used}/{total} used in the last 24h")
    from .threads_api import ThreadsAPIError

    since = int((_now() - timedelta(days=7)).timestamp())
    try:
        posts = threads.keyword_search("postpartum", since=since, limit=25)
    except ThreadsAPIError as exc:
        print(f"Search failed: {exc}\nCheck that your app has the threads_keyword_search permission.")
        return
    others = [p for p in posts if p.username != username]
    if others:
        print(f"Search sees public posts: {len(others)} from other people for 'postpartum' this week.")
    else:
        print(
            "Search returned no posts from other people. Until Meta approves the\n"
            "threads_keyword_search permission, search only covers your own posts."
        )


def cmd_discover(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .drafter import ClaudeDrafter
    from .pipeline import discover

    threads = _threads_client()
    drafter = ClaudeDrafter(cfg.voice, cfg.site, cfg.model)
    print(f"Searching {len(cfg.search.keywords) + len(cfg.search.tags)} queries...")
    result = discover(cfg, threads, store, drafter, _now())
    print(
        f"Found {result.found} matching posts, {result.new} new. "
        f"Drafted {result.drafted} replies; skipped {sum(result.skipped.values())}."
    )
    for reason, count in result.skipped.most_common():
        print(f"  skipped {count}: {reason}")
    if result.errors:
        print(f"{len(result.errors)} errors (see above). Failed drafts are retried next run.")
    if result.drafted:
        print("Next: outreach review")


def cmd_review(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .review import review

    counts = review(cfg, store, now_fn=_now)
    if not any(counts.values()):
        return
    print(f"\nApproved {counts['approved']}, skipped {counts['skipped']}, left {counts['left']} for later.")
    if counts["approved"]:
        print("Next: outreach post --dry-run, then outreach post")


def cmd_post(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .pipeline import post_approved

    threads = _threads_client()
    result = post_approved(
        cfg, threads, store, now_fn=_now, sleep=time.sleep, dry_run=args.dry_run, max_posts=args.max
    )
    verb = "Would post" if args.dry_run else "Posted"
    print(f"{verb} {result.posted}; failed {result.failed}; skipped {sum(result.skipped.values())}.")
    if result.stopped_because:
        print(f"Stopped: {result.stopped_because}.")


def cmd_stats(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    stats = store.stats()
    window = _now() - timedelta(days=cfg.site.ratio_window_days)
    with_link, total = store.link_counts(window)
    stats["link_share_last_window"] = f"{with_link}/{total} (cap {cfg.site.max_link_ratio:.0%})"
    stats["recent_replies"] = store.recent_replies(10)
    print(to_json(stats))


def cmd_purge(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    count = store.purge(_now() - timedelta(days=cfg.limits.retention_days))
    print(f"Wiped text from {count} posts older than {cfg.limits.retention_days} days.")


COMMANDS = {
    "check": cmd_check,
    "discover": cmd_discover,
    "review": cmd_review,
    "post": cmd_post,
    "stats": cmd_stats,
    "purge": cmd_purge,
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="outreach", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        if name == "post":
            p.add_argument("--dry-run", action="store_true", help="show what would be posted")
            p.add_argument("--max", type=int, default=None, help="post at most this many now")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        sys.exit(str(exc))
    store = Store(cfg.db_path)
    try:
        COMMANDS[args.command](cfg, store, args)
    finally:
        store.close()


if __name__ == "__main__":
    main()
