"""Approving drafts: interactively in a terminal, or one at a time from the CLI."""

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


def category_of(row: Row) -> Category | None:
    return Category(row.category) if row.category else None


def can_link(row: Row, cfg: Config | None = None) -> bool:
    if row.kind != "post" and cfg is not None and not cfg.site.links_in_replies:
        return False
    return policy.link_allowed(category_of(row), row.draft_link_line or "", kind=row.kind)


def compose_row(cfg: Config, row: Row, include_link: bool) -> str:
    return policy.compose(
        category_of(row), row.draft_body or "", row.draft_link_line or "", cfg.site.link_url,
        include_link, kind=row.kind,
    )


def link_budget_ok(cfg: Config, store: Store, now: datetime) -> bool:
    with_link, total = store.link_counts(now - timedelta(days=cfg.site.ratio_window_days))
    return policy.link_budget_available(with_link, total, cfg.site.max_link_ratio)


def approve_text(cfg: Config, store: Store, row: Row, text: str, now: datetime) -> list[str]:
    """Approves `text` for `row` if it passes every rule. Returns the problems otherwise."""
    problems = policy.validate(text, cfg.site.link_url)
    has_link = cfg.site.link_url in text
    if has_link and row.kind != "post" and not cfg.site.links_in_replies:
        problems.append("links are turned off for replies (site.links_in_replies)")
    elif has_link and not link_budget_ok(cfg, store, now):
        problems.append("the link budget is used up for this week; approve it without the link")
    if not problems:
        store.approve(row.id, text, has_link, now)
    return problems


def _ago(timestamp: str, now: datetime) -> str:
    ts = parse_ts(timestamp)
    if ts is None:
        return "?"
    hours = (now - ts).total_seconds() / 3600
    return f"{hours * 60:.0f}m ago" if hours < 1 else f"{hours:.0f}h ago"


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(
        textwrap.fill(line, 68, initial_indent=prefix, subsequent_indent=prefix,
                      break_long_words=False, break_on_hyphens=False) if line else ""
        for line in text.splitlines()
    )


def describe(row: Row, text: str, now: datetime, index: str = "") -> str:
    lines = [RULE]
    if row.kind == "post":
        lines.append(f"{index}NEW POST · {row.rationale or ''}   id {row.id}")
    elif row.kind == "comment":
        lines.append(f"{index}@{row.username} commented on your post · {_ago(row.posted_at, now)}   id {row.id}")
        lines.append(_indent((row.context or "")[:200], "  │ "))
        lines += ["", _indent(row.text)]
    else:
        lines.append(f"{index}@{row.username} · {_ago(row.posted_at, now)} · found via \"{row.query}\"   id {row.id}")
        lines += ["", _indent(row.text)]
    if row.permalink:
        lines.append(f"  {row.permalink}")
    if row.kind != "post":
        lines += ["", f"  {row.category}: {row.rationale or ''}"]
    label = "Your post" if row.kind == "post" else "Your reply"
    lines += ["", f"  {label} ({len(text)}/{policy.MAX_POST_CHARS}):", _indent(text)]
    return "\n".join(lines)


def edit_with_editor(text: str) -> str:
    editor = os.environ.get("EDITOR")
    if not editor:
        return input("New text (one line, Enter to keep current):\n> ").strip() or text
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
        out("Nothing to review. Run `outreach inbox` or `outreach compose` first.")
        return counts

    link_url = cfg.site.link_url
    for i, row in enumerate(drafts, start=1):
        include_link = can_link(row, cfg) and link_budget_ok(cfg, store, now_fn())
        edited: str | None = None

        while True:
            text = edited if edited is not None else compose_row(cfg, row, include_link)
            out(describe(row, text, now_fn(), f"[{i}/{len(drafts)}]  "))
            for err in policy.validate(text, link_url):
                out(f"  ✗ {err}")
            for flag in policy.warnings(text):
                out(f"  ⚠ {flag}")

            choice = prompt("[a]pprove  [e]dit  [l]ink on/off  [s]kip  [q]uit > ").strip().lower()
            if choice == "a":
                problems = approve_text(cfg, store, row, text, now_fn())
                if problems:
                    out("  Can't approve yet: " + "; ".join(problems))
                    continue
                counts["approved"] += 1
                break
            if choice == "e":
                edited = edit(text)
                continue
            if choice == "l":
                if not can_link(row, cfg):
                    out("  No link for this one (none drafted, or links are off for replies).")
                    continue
                if not include_link and not link_budget_ok(cfg, store, now_fn()):
                    out("  The link budget is used up for this week.")
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
                counts["left"] = len(drafts) - i + 1
                return counts
            out("  Type a, e, l, s or q.")

    return counts
