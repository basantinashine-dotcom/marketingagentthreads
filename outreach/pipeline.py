"""The two automated steps: discover (search + draft) and post (publish approved replies).

Review sits between them and is always done by a person (see review.py).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import anthropic

from . import policy
from .config import Config
from .drafter import Drafter
from .store import Store
from .threads_api import ThreadsAPIError, ThreadsClient, ThreadsPost

Log = Callable[[str], None]


def parse_ts(value: str) -> datetime | None:
    """Threads timestamps look like 2026-11-19T17:24:36+0000."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _older_than(timestamp: str, now: datetime, hours: int) -> bool:
    ts = parse_ts(timestamp)
    return ts is not None and ts < now - timedelta(hours=hours)


@dataclass
class DiscoverResult:
    queries: int = 0
    found: int = 0
    new: int = 0
    drafted: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


def discover(
    cfg: Config,
    threads: ThreadsClient,
    store: Store,
    drafter: Drafter,
    now: datetime,
    log: Log = print,
) -> DiscoverResult:
    result = DiscoverResult()
    _, my_username = threads.me()
    since = int((now - timedelta(hours=cfg.search.max_post_age_hours)).timestamp())

    queries = [(q, False) for q in cfg.search.keywords] + [(t, True) for t in cfg.search.tags]
    for query, is_tag in queries:
        result.queries += 1
        try:
            posts = threads.keyword_search(
                query,
                search_type=cfg.search.search_type,
                tag=is_tag,
                since=since,
                limit=cfg.search.results_per_query,
            )
        except ThreadsAPIError as exc:
            result.errors.append(f"search {query!r}: {exc}")
            log(f"  ! search {query!r} failed: {exc}")
            continue
        for post in posts:
            if post.username and post.username == my_username:
                continue
            if len(post.text.strip()) < cfg.search.min_text_length:
                continue
            result.found += 1
            label = f"#{query}" if is_tag else query
            if store.add_post(post, label, now):
                result.new += 1

    cooldown_since = now - timedelta(days=cfg.limits.author_cooldown_days)
    drafted_authors: set[str] = set()
    for row in store.with_status("new", limit=cfg.limits.max_drafts_per_run):
        if _older_than(row.posted_at, now, cfg.search.max_post_age_hours):
            store.skip(row.id, "stale")
            result.skipped["stale"] += 1
            continue
        if row.username in drafted_authors or store.recently_engaged(
            row.username, cooldown_since, exclude_id=row.id
        ):
            store.skip(row.id, "author_cooldown")
            result.skipped["author_cooldown"] += 1
            continue

        post = ThreadsPost(
            id=row.id, text=row.text, username=row.username, permalink=row.permalink, timestamp=row.posted_at
        )
        try:
            assessment = drafter.assess(post, row.query)
        except (anthropic.APIError, RuntimeError) as exc:
            # Leave it as "new" so the next run retries it.
            result.errors.append(f"draft {row.id}: {exc}")
            log(f"  ! drafting {row.id} failed: {exc}")
            continue

        if assessment is None:
            store.skip(row.id, "model:declined")
            result.skipped["model:declined"] += 1
            continue

        decision = policy.decide(assessment.category, assessment.should_reply, assessment.reply_body)
        drafted = decision.status == "drafted"
        store.save_draft(
            row.id,
            status=decision.status,
            category=assessment.category.value,
            rationale=assessment.rationale,
            body=assessment.reply_body if drafted else "",
            link_line=assessment.link_line if drafted else "",
            skip_reason=decision.reason,
        )
        if drafted:
            result.drafted += 1
            drafted_authors.add(row.username)
        else:
            result.skipped[decision.reason] += 1

    return result


@dataclass
class InboxResult:
    posts_checked: int = 0
    new_comments: int = 0
    drafted: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    # Comments that will never get an automatic reply but that you may want to see.
    needs_you: list[tuple[str, str, str]] = field(default_factory=list)  # (category, @user, link)
    errors: list[str] = field(default_factory=list)


def inbox(
    cfg: Config,
    threads: ThreadsClient,
    store: Store,
    drafter: Drafter | None,
    now: datetime,
    log: Log = print,
) -> InboxResult:
    """Collects new comments on your recent posts and, given a drafter, drafts replies."""
    result = InboxResult()
    _, my_username = threads.me()
    since = int((now - timedelta(days=cfg.limits.comment_lookback_days)).timestamp())

    for post in threads.my_posts(since=since, limit=25):
        result.posts_checked += 1
        try:
            comments = threads.conversation(post.id)
        except ThreadsAPIError as exc:
            result.errors.append(f"comments on {post.id}: {exc}")
            log(f"  ! couldn't read comments on {post.permalink or post.id}: {exc}")
            continue
        mine = {post.id} | {c.id for c in comments if c.username == my_username}
        answered = {c.replied_to_id for c in comments if c.username == my_username}
        for comment in comments:
            if comment.username == my_username or comment.hidden or not comment.text.strip():
                continue
            if comment.id in answered:
                continue  # you've already replied, here or in the app
            if comment.replied_to_id and comment.replied_to_id not in mine:
                continue  # a side conversation between other people
            if store.add_post(comment, "comment", now, kind="comment", context=post.text):
                result.new_comments += 1

    if drafter is None:
        return result

    for row in store.with_status("new", limit=cfg.limits.max_drafts_per_run, kind="comment"):
        comment = ThreadsPost(
            id=row.id, text=row.text, username=row.username, permalink=row.permalink, timestamp=row.posted_at
        )
        try:
            assessment = drafter.assess(comment, context=row.context)
        except (anthropic.APIError, RuntimeError) as exc:
            result.errors.append(f"draft {row.id}: {exc}")
            log(f"  ! drafting a reply to @{row.username} failed: {exc}")
            continue
        if assessment is None:
            store.skip(row.id, "model:declined")
            result.skipped["model:declined"] += 1
            continue
        decision = policy.decide(assessment.category, assessment.should_reply, assessment.reply_body)
        drafted = decision.status == "drafted"
        store.save_draft(
            row.id,
            status=decision.status,
            category=assessment.category.value,
            rationale=assessment.rationale,
            body=assessment.reply_body if drafted else "",
            link_line=assessment.link_line if drafted else "",
            skip_reason=decision.reason,
        )
        if drafted:
            result.drafted += 1
        else:
            result.skipped[decision.reason] += 1
            if assessment.category in policy.NEEDS_YOU:
                result.needs_you.append((assessment.category.value, row.username, row.permalink))
    return result


def compose_new_posts(
    cfg: Config, threads: ThreadsClient, store: Store, drafter, count: int, now: datetime
) -> list[str]:
    """Drafts `count` new posts in your voice. Returns their ids."""
    recent = [p.text for p in threads.my_posts(limit=15)]
    return [
        store.add_new_post(idea.body, idea.link_line, idea.theme, now)
        for idea in drafter.compose_posts(count, recent)
    ]


@dataclass
class PostResult:
    posted: int = 0
    failed: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    stopped_because: str = ""


REPLY_KINDS = ("search", "comment")


def post_approved(
    cfg: Config,
    threads: ThreadsClient,
    store: Store,
    *,
    now_fn: Callable[[], datetime],
    sleep: Callable[[float], None],
    dry_run: bool = False,
    max_posts: int | None = None,
    log: Log = print,
) -> PostResult:
    result = PostResult()
    now = now_fn()
    day_ago = now - timedelta(hours=24)
    reply_budget = cfg.limits.max_posts_per_day - store.posted_since(day_ago, kinds=REPLY_KINDS)
    post_budget = cfg.limits.max_new_posts_per_day - store.posted_since(day_ago, kinds=("post",))
    total_budget = max_posts if max_posts is not None else reply_budget + post_budget

    approved = store.with_status("approved")
    if not approved:
        result.stopped_because = "nothing approved to post"
        return result

    reply_hold = "daily cap for replies"
    if not dry_run and any(r.kind in REPLY_KINDS for r in approved):
        used, total = threads.reply_quota()
        if used >= total:
            reply_budget = 0
            reply_hold = "Meta reply quota"
            log(f"  Meta reply quota used up ({used}/{total} in 24h); only new posts will go out.")

    cooldown_since = now - timedelta(days=cfg.limits.author_cooldown_days)
    held: Counter[str] = Counter()
    for row in approved:
        if result.posted >= total_budget:
            result.stopped_because = "reached this run's posting limit"
            break
        is_post = row.kind == "post"
        if is_post and post_budget <= 0:
            held["daily cap for new posts"] += 1
            continue
        if not is_post and reply_budget <= 0:
            held[reply_hold] += 1
            continue

        text = row.final_text or ""
        errors = policy.validate(text, cfg.site.link_url)
        if errors:
            store.mark_failed(row.id, "; ".join(errors))
            result.failed += 1
            log(f"  ! {row.id}: {'; '.join(errors)}")
            continue
        if row.kind == "search":
            if store.recently_engaged(row.username, cooldown_since, exclude_id=row.id, include_queued=False):
                store.skip(row.id, "author_cooldown")
                result.skipped["author_cooldown"] += 1
                continue
            if _older_than(row.posted_at, now_fn(), cfg.limits.max_reply_age_hours):
                store.skip(row.id, "stale")
                result.skipped["stale"] += 1
                continue

        target = "new post" if is_post else f"reply to @{row.username}"
        if dry_run:
            log(f"  would publish {target}:\n    {text}")
            result.posted += 1
            post_budget, reply_budget = (post_budget - 1, reply_budget) if is_post else (post_budget, reply_budget - 1)
            continue

        last = store.last_replied_at()
        if last is not None:
            wait = cfg.limits.seconds_between_posts - (now_fn() - last).total_seconds()
            if wait > 0:
                log(f"  waiting {wait:.0f}s between posts...")
                sleep(wait)

        try:
            media_id = threads.publish(text) if is_post else threads.reply(row.id, text)
        except ThreadsAPIError as exc:
            store.mark_failed(row.id, str(exc))
            result.failed += 1
            log(f"  ! {target} failed: {exc}")
            # Stop rather than skip ahead, so approved items never go out of order.
            result.stopped_because = f"publishing failed ({exc}); fix it, then `outreach approve {row.id}`"
            break
        try:
            link = threads.permalink(media_id)
        except ThreadsAPIError:
            link = ""
        store.mark_posted(row.id, media_id, link, now_fn())
        result.posted += 1
        if is_post:
            post_budget -= 1
        else:
            reply_budget -= 1
        log(f"  published {target}: {link or media_id}")

    if held and not result.stopped_because:
        result.stopped_because = ", ".join(f"{n} waiting on the {why}" for why, n in held.items())
    return result
