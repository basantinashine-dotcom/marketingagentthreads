"""SQLite state: every discovered post moves through one row.

new -> drafted | skipped -> approved | rejected -> posted | failed | skipped
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .threads_api import ThreadsPost

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id              TEXT PRIMARY KEY,
    username        TEXT,
    text            TEXT,
    permalink       TEXT,
    posted_at       TEXT,
    query           TEXT,
    discovered_at   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'new',
    skip_reason     TEXT,
    category        TEXT,
    rationale       TEXT,
    draft_body      TEXT,
    draft_link_line TEXT,
    final_text      TEXT,
    includes_link   INTEGER NOT NULL DEFAULT 0,
    reviewed_at     TEXT,
    reply_id        TEXT,
    reply_permalink TEXT,
    replied_at      TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS posts_username ON posts(username);
"""


@dataclass(frozen=True)
class Row:
    id: str
    username: str
    text: str
    permalink: str
    posted_at: str
    query: str
    status: str
    category: str | None
    rationale: str | None
    draft_body: str | None
    draft_link_line: str | None
    final_text: str | None
    includes_link: bool


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- discovery -----------------------------------------------------------

    def add_post(self, post: ThreadsPost, query: str, now: datetime) -> bool:
        cur = self._db.execute(
            "INSERT OR IGNORE INTO posts (id, username, text, permalink, posted_at, query, discovered_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (post.id, post.username, post.text, post.permalink, post.timestamp, query, _iso(now)),
        )
        self._db.commit()
        return cur.rowcount == 1

    def with_status(self, status: str, limit: int | None = None) -> list[Row]:
        sql = "SELECT * FROM posts WHERE status = ? ORDER BY discovered_at"
        args: tuple = (status,)
        if limit is not None:
            sql += " LIMIT ?"
            args += (limit,)
        return [_row(r) for r in self._db.execute(sql, args)]

    def save_draft(
        self,
        post_id: str,
        *,
        status: str,
        category: str,
        rationale: str,
        body: str,
        link_line: str,
        skip_reason: str = "",
    ) -> None:
        self._db.execute(
            "UPDATE posts SET status = ?, category = ?, rationale = ?, draft_body = ?,"
            " draft_link_line = ?, skip_reason = ? WHERE id = ?",
            (status, category, rationale, body, link_line, skip_reason or None, post_id),
        )
        self._db.commit()

    def skip(self, post_id: str, reason: str) -> None:
        self._db.execute("UPDATE posts SET status = 'skipped', skip_reason = ? WHERE id = ?", (reason, post_id))
        self._db.commit()

    # -- review --------------------------------------------------------------

    def approve(self, post_id: str, final_text: str, includes_link: bool, now: datetime) -> None:
        self._db.execute(
            "UPDATE posts SET status = 'approved', final_text = ?, includes_link = ?, reviewed_at = ?"
            " WHERE id = ?",
            (final_text, int(includes_link), _iso(now), post_id),
        )
        self._db.commit()

    def reject(self, post_id: str, now: datetime) -> None:
        self._db.execute(
            "UPDATE posts SET status = 'rejected', reviewed_at = ? WHERE id = ?", (_iso(now), post_id)
        )
        self._db.commit()

    # -- posting -------------------------------------------------------------

    def mark_posted(self, post_id: str, reply_id: str, reply_permalink: str, now: datetime) -> None:
        self._db.execute(
            "UPDATE posts SET status = 'posted', reply_id = ?, reply_permalink = ?, replied_at = ?,"
            " error = NULL WHERE id = ?",
            (reply_id, reply_permalink, _iso(now), post_id),
        )
        self._db.commit()

    def mark_failed(self, post_id: str, error: str) -> None:
        self._db.execute("UPDATE posts SET status = 'failed', error = ? WHERE id = ?", (error, post_id))
        self._db.commit()

    # -- guards --------------------------------------------------------------

    def recently_engaged(
        self, username: str, since: datetime, *, exclude_id: str | None = None, include_queued: bool = True
    ) -> bool:
        """True if we replied to this author since `since` (or, with include_queued,
        already have a draft or approved reply waiting for them)."""
        queued = " OR status IN ('approved', 'drafted')" if include_queued else ""
        row = self._db.execute(
            "SELECT 1 FROM posts WHERE username = ? AND id IS NOT ?"
            f" AND ((status = 'posted' AND replied_at >= ?){queued}) LIMIT 1",
            (username, exclude_id, _iso(since)),
        ).fetchone()
        return row is not None

    def last_replied_at(self) -> datetime | None:
        row = self._db.execute("SELECT MAX(replied_at) FROM posts WHERE status = 'posted'").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def posted_since(self, since: datetime) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM posts WHERE status = 'posted' AND replied_at >= ?", (_iso(since),)
        ).fetchone()[0]

    def link_counts(self, since: datetime) -> tuple[int, int]:
        """(replies with link, all replies) among posted + approved since `since`."""
        row = self._db.execute(
            "SELECT COALESCE(SUM(includes_link), 0), COUNT(*) FROM posts"
            " WHERE (status = 'posted' AND replied_at >= ?) OR (status = 'approved' AND reviewed_at >= ?)",
            (_iso(since), _iso(since)),
        ).fetchone()
        return int(row[0]), int(row[1])

    # -- reporting & retention ----------------------------------------------

    def stats(self) -> dict[str, object]:
        by_status = dict(self._db.execute("SELECT status, COUNT(*) FROM posts GROUP BY status").fetchall())
        by_category = dict(
            self._db.execute(
                "SELECT category, COUNT(*) FROM posts WHERE status = 'posted' GROUP BY category"
            ).fetchall()
        )
        skips = dict(
            self._db.execute(
                "SELECT skip_reason, COUNT(*) FROM posts WHERE status = 'skipped' GROUP BY skip_reason"
            ).fetchall()
        )
        with_link = self._db.execute(
            "SELECT COUNT(*) FROM posts WHERE status = 'posted' AND includes_link = 1"
        ).fetchone()[0]
        return {"by_status": by_status, "posted_by_category": by_category, "skip_reasons": skips,
                "posted_with_link": with_link}

    def recent_replies(self, limit: int = 20) -> list[dict[str, str]]:
        rows = self._db.execute(
            "SELECT replied_at, category, includes_link, reply_permalink FROM posts"
            " WHERE status = 'posted' ORDER BY replied_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def purge(self, before: datetime) -> int:
        """Wipe post content older than `before`, keeping ids so posts aren't rediscovered."""
        cur = self._db.execute(
            "UPDATE posts SET username = NULL, text = NULL, permalink = NULL, rationale = NULL,"
            " draft_body = NULL, draft_link_line = NULL, final_text = NULL"
            " WHERE COALESCE(replied_at, discovered_at) < ? AND text IS NOT NULL"
            " AND status NOT IN ('drafted', 'approved')",
            (_iso(before),),
        )
        self._db.commit()
        return cur.rowcount


def _row(r: sqlite3.Row) -> Row:
    return Row(
        id=r["id"],
        username=r["username"] or "",
        text=r["text"] or "",
        permalink=r["permalink"] or "",
        posted_at=r["posted_at"] or "",
        query=r["query"] or "",
        status=r["status"],
        category=r["category"],
        rationale=r["rationale"],
        draft_body=r["draft_body"],
        draft_link_line=r["draft_link_line"],
        final_text=r["final_text"],
        includes_link=bool(r["includes_link"]),
    )


def to_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def window_start(now: datetime, *, days: int = 0, hours: int = 0) -> datetime:
    return now - timedelta(days=days, hours=hours)
