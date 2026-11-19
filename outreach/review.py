"""Interactive review: a person approves, edits or skips every draft."""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
from collections.abc import Callable
from datetime import datetime, timedelta

from . import policy
from .config import Config
from .pipeline import parse_ts
from .policy import Category
from .store import Row, Store

Prompt = Callable[[str], str]
Out = Callable[[str], None]

RULE = "-" * 72


def _ago(timestamp: str, now: datetime) -> str:
    ts = parse_ts(timestamp)
    if ts is None:
        return "?"
    hours = (now - ts).total_seconds() / 3600
    return f"{hours * 60:.0f}m ago" if hours < 1 else f"{hours:.0f}h ago"


def _indent(text: str) -> str:
    return "\n".join(
        textwrap.fill(
            line, 68, initial_indent="    ", subsequent_indent="    ",
            break_long_words=False, break_on_hyphens=False,
        )
        if line
        else ""
        for line in text.splitlines()
    )


def edit_with_editor(text: str) -> str:
    editor = os.environ.get("EDITOR")
    if not editor:
        return input("New reply (one line, Enter to keep current):\n> ").strip() or text
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as fh:
        fh.write(text)
        path = fh.name
    try:
        subprocess.run([editor, path], check=False)
        with open(path) as fh:
            return fh.read().strip()
    finally:
        os.unlink(path)


def review(
    cfg: Config,
    store: Store,
    *,
    now_fn: Callable[[], datetime],
    prompt: Prompt = input,
    out: Out = print,
    edit: Callable[[str], str] = edit_with_editor,
) -> dict[str, int]:
    counts = {"approved": 0, "skipped": 0, "left": 0}
    drafts = store.with_status("drafted")
    if not drafts:
        out("Nothing to review. Run `outreach discover` first.")
        return counts

    link_url = cfg.site.link_url
    for index, row in enumerate(drafts, start=1):
        category = Category(row.category or Category.NOT_RELEVANT)
        link_line = row.draft_link_line or ""
        body = row.draft_body or ""
        include_link = policy.link_allowed(category, link_line) and _link_budget_ok(cfg, store, now_fn())
        edited: str | None = None

        while True:
            text = edited if edited is not None else policy.compose(
                category, body, link_line, link_url, include_link
            )
            errors = policy.validate(text, link_url)
            flags = policy.warnings(text)
            _show(out, index, len(drafts), row, category, text, errors, flags, now_fn())

            choice = prompt("[a]pprove  [e]dit  [l]ink on/off  [s]kip  [q]uit > ").strip().lower()
            if choice == "a":
                has_link = link_url in text
                if errors:
                    out("  Can't approve until these are fixed. Edit the reply or skip it.")
                    continue
                if has_link and not _link_budget_ok(cfg, store, now_fn()):
                    out("  Link budget used up for this week. Turn the link off or skip.")
                    continue
                store.approve(row.id, text, has_link, now_fn())
                counts["approved"] += 1
                break
            if choice == "e":
                edited = edit(text)
                continue
            if choice == "l":
                if not policy.link_allowed(category, link_line):
                    out("  No link line was drafted for this post.")
                    continue
                if not include_link and not _link_budget_ok(cfg, store, now_fn()):
                    with_link, total = store.link_counts(_window(cfg, now_fn()))
                    out(f"  Link budget used: {with_link} of your last {total} replies have it.")
                    continue
                include_link = not include_link
                if edited is not None:
                    out("  (Link toggled; your edit was reset.)")
                    edited = None
                continue
            if choice == "s":
                store.reject(row.id, now_fn())
                counts["skipped"] += 1
                break
            if choice == "q":
                counts["left"] = len(drafts) - index + 1
                return counts
            out("  Type a, e, l, s or q.")

    return counts


def _window(cfg: Config, now: datetime) -> datetime:
    return now - timedelta(days=cfg.site.ratio_window_days)


def _link_budget_ok(cfg: Config, store: Store, now: datetime) -> bool:
    with_link, total = store.link_counts(_window(cfg, now))
    return policy.link_budget_available(with_link, total, cfg.site.max_link_ratio)


def _show(
    out: Out,
    index: int,
    total: int,
    row: Row,
    category: Category,
    text: str,
    errors: list[str],
    flags: list[str],
    now: datetime,
) -> None:
    out(RULE)
    out(f"[{index}/{total}]  @{row.username} · {_ago(row.posted_at, now)} · found via \"{row.query}\"")
    if row.permalink:
        out(f"  {row.permalink}")
    out("")
    out(_indent(row.text))
    out("")
    out(f"  {category.value}: {row.rationale or ''}")
    out("")
    out(f"  Your reply ({len(text)}/{policy.MAX_POST_CHARS}):")
    out(_indent(text))
    for err in errors:
        out(f"  ✗ {err}")
    for flag in flags:
        out(f"  ⚠ {flag}")
