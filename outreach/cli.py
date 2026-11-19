"""outreach: grow conversations on Threads from your own account.

Your posts and the replies under them (works with standard Threads API access):
    outreach inbox            collect new comments on your recent posts and draft replies
    outreach compose          draft new posts in your voice
    outreach review           approve, edit or skip each draft (nothing posts without this)
    outreach post             publish what you approved, paced and capped

Approving one at a time instead of `review`:
    outreach list             show drafts waiting for you
    outreach approve ID       approve a draft (--no-link to drop the link)
    outreach skip ID          skip a draft
    outreach draft ...        save a draft written by hand (or by another assistant)

Other:
    outreach check            verify your token and what it can do
    outreach discover         search strangers' posts (needs Meta App Review approval)
    outreach stats            counts, link share, recent posts and replies
    outreach purge            wipe stored text older than the retention window
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


def _has_claude_credentials() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


def _drafter(cfg: Config):
    from .drafter import ClaudeDrafter

    return ClaudeDrafter(cfg.voice, cfg.site, cfg.model)


def _find(store: Store, ref: str):
    row = store.find(ref)
    if row is None:
        sys.exit(f"No draft matches {ref!r}. Use the id shown by `outreach list`.")
    return row


# -- commands -----------------------------------------------------------------


def cmd_check(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .threads_api import ThreadsAPIError

    threads = _threads_client()
    user_id, username = threads.me()
    print(f"✓ Token works: @{username}")

    def attempt(label: str, fn) -> object | None:
        try:
            value = fn()
        except ThreadsAPIError as exc:
            print(f"✗ {label}: {exc}")
            return None
        return value

    posts = attempt("read your posts", lambda: threads.my_posts(limit=5))
    if posts is not None:
        print(f"✓ Can read your posts ({len(posts)} recent)")
        if posts:
            comments = attempt(
                "read comments on your posts (needs threads_read_replies)",
                lambda: threads.conversation(posts[0].id),
            )
            if comments is not None:
                print(f"✓ Can read comments ({len(comments)} on your latest post)")
    quota = attempt("read your reply quota", threads.reply_quota)
    if quota is not None:
        print(f"✓ Reply quota: {quota[0]}/{quota[1]} used in the last 24h")
    print("· Posting isn't tested here; `outreach post --dry-run` shows what would go out.")


def cmd_inbox(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .pipeline import inbox

    drafter = None
    if not args.no_draft:
        if _has_claude_credentials():
            drafter = _drafter(cfg)
        else:
            print("No Claude API key found, so comments are collected but not drafted.")
    result = inbox(cfg, _threads_client(), store, drafter, _now())
    print(f"Checked {result.posts_checked} of your posts: {result.new_comments} new comments.")
    if drafter:
        print(f"Drafted {result.drafted} replies; skipped {sum(result.skipped.values())}.")
        for reason, count in result.skipped.most_common():
            print(f"  skipped {count}: {reason}")
    for category, user, link in result.needs_you:
        print(f"  ! @{user} left a comment that looks like {category.replace('_', ' ')}. "
              f"No automatic reply. You may want to look: {link}")
    if result.drafted:
        print("Next: outreach review")


def cmd_compose(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .pipeline import compose_new_posts

    if not _has_claude_credentials():
        sys.exit("`compose` needs a Claude API key (ANTHROPIC_API_KEY).")
    ids = compose_new_posts(cfg, _threads_client(), store, _drafter(cfg), args.count, _now())
    print(f"Drafted {len(ids)} new posts. Next: outreach review")


def cmd_list(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .policy import validate, warnings
    from .review import can_link, compose_row, describe, link_budget_ok

    rows = store.with_status(args.status)
    if not rows:
        print(f"Nothing with status {args.status!r}.")
        return
    now = _now()
    for row in rows:
        if args.status == "drafted":
            text = compose_row(cfg, row, can_link(row) and link_budget_ok(cfg, store, now))
        else:
            text = row.final_text or row.draft_body or ""
        print(describe(row, text, now))
        for err in validate(text, cfg.site.link_url):
            print(f"  ✗ {err}")
        for flag in warnings(text):
            print(f"  ⚠ {flag}")
    print(f"\n{len(rows)} {args.status}.")


def cmd_draft(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .policy import Category, decide

    link_line = args.link_line or ""
    if args.new_post:
        post_id = store.add_new_post(args.body, link_line, args.theme or "", _now())
        print(f"Saved new post draft {post_id}.")
        return
    if not args.id or not args.category:
        sys.exit("Give the comment's ID and --category, or use --new-post.")
    row = _find(store, args.id)
    category = Category(args.category)
    decision = decide(category, True, args.body)
    store.save_draft(row.id, status=decision.status, category=category.value,
                     rationale=args.why or "", body=args.body if decision.status == "drafted" else "",
                     link_line=link_line, skip_reason=decision.reason)
    print(f"{row.id}: {decision.status}" + (f" ({decision.reason})" if decision.reason else ""))


def cmd_approve(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .review import approve_text, can_link, compose_row, link_budget_ok

    now = _now()
    for ref in args.ids:
        row = _find(store, ref)
        if row.status != "drafted":
            print(f"{row.id}: is {row.status}, not a draft; left alone.")
            continue
        want_link = not args.no_link and can_link(row)
        if want_link and not link_budget_ok(cfg, store, now):
            print(f"{row.id}: link budget used up this week, so approving without the link.")
            want_link = False
        text = args.text or compose_row(cfg, row, want_link)
        problems = approve_text(cfg, store, row, text, now)
        if problems:
            print(f"{row.id}: not approved: " + "; ".join(problems))
        else:
            print(f"{row.id}: approved" + (" (with link)" if cfg.site.link_url in text else ""))


def cmd_skip(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    for ref in args.ids:
        row = _find(store, ref)
        store.reject(row.id, _now())
        print(f"{row.id}: skipped")


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

    result = post_approved(
        cfg, _threads_client(), store, now_fn=_now, sleep=time.sleep, dry_run=args.dry_run, max_posts=args.max
    )
    verb = "Would publish" if args.dry_run else "Published"
    print(f"{verb} {result.posted}; failed {result.failed}; skipped {sum(result.skipped.values())}.")
    if result.stopped_because:
        print(f"Stopped: {result.stopped_because}.")


def cmd_discover(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    from .pipeline import discover

    if not _has_claude_credentials():
        sys.exit("`discover` needs a Claude API key (ANTHROPIC_API_KEY).")
    result = discover(cfg, _threads_client(), store, _drafter(cfg), _now())
    print(f"Found {result.found} matching posts, {result.new} new. Drafted {result.drafted}; "
          f"skipped {sum(result.skipped.values())}.")
    if result.errors:
        print(f"{len(result.errors)} errors. Search needs Meta's approval of threads_keyword_search.")


def cmd_stats(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    stats = store.stats()
    with_link, total = store.link_counts(_now() - timedelta(days=cfg.site.ratio_window_days))
    stats["link_share_last_window"] = f"{with_link}/{total} (cap {cfg.site.max_link_ratio:.0%})"
    stats["recent"] = store.recent_replies(10)
    print(to_json(stats))


def cmd_purge(cfg: Config, store: Store, args: argparse.Namespace) -> None:
    count = store.purge(_now() - timedelta(days=cfg.limits.retention_days))
    print(f"Wiped text from {count} rows older than {cfg.limits.retention_days} days.")


COMMANDS = {
    "check": cmd_check, "inbox": cmd_inbox, "compose": cmd_compose, "review": cmd_review,
    "list": cmd_list, "draft": cmd_draft, "approve": cmd_approve, "skip": cmd_skip,
    "post": cmd_post, "discover": cmd_discover, "stats": cmd_stats, "purge": cmd_purge,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="outreach", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        if name == "inbox":
            p.add_argument("--no-draft", action="store_true", help="collect comments without drafting")
        if name == "compose":
            p.add_argument("--count", type=int, default=3)
        if name == "list":
            p.add_argument("--status", default="drafted", help="drafted, new, approved, posted, skipped...")
        if name == "draft":
            p.add_argument("id", nargs="?", help="comment id (omit with --new-post)")
            p.add_argument("--new-post", action="store_true")
            p.add_argument("--body", required=True)
            p.add_argument("--link-line", default="", help="sentence containing [LINK]")
            p.add_argument("--category", help="for replies: seeking_support, venting, ...")
            p.add_argument("--theme", help="for new posts: a few words naming the idea")
            p.add_argument("--why", help="one line for the reviewer")
        if name in ("approve", "skip"):
            p.add_argument("ids", nargs="+", help="draft ids (or their last 4+ characters)")
        if name == "approve":
            p.add_argument("--no-link", action="store_true")
            p.add_argument("--text", help="approve this exact text instead of the draft")
        if name == "post":
            p.add_argument("--dry-run", action="store_true", help="show what would be published")
            p.add_argument("--max", type=int, default=None, help="publish at most this many now")
    return parser


def main(argv: list[str] | None = None) -> None:
    from .threads_api import ThreadsAPIError

    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        sys.exit(str(exc))
    store = Store(cfg.db_path)
    try:
        COMMANDS[args.command](cfg, store, args)
    except ThreadsAPIError as exc:
        hint = ""
        if exc.code == 190:
            hint = "\nYour Threads token is invalid or expired. Generate a new one and save it again."
        elif exc.code == 10:
            hint = "\nYour token is missing a permission this needs. Regenerate it after adding the permission."
        sys.exit(f"{exc}{hint}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
