from __future__ import annotations

from outreach.drafter import Assessment
from outreach.policy import Category
from outreach.threads_api import ThreadsAPIError, ThreadsPost


class FakeThreads:
    def __init__(self, results: dict[str, list[ThreadsPost]] | None = None, me: str = "me"):
        self.results = results or {}
        self.username = me
        self.replies: list[tuple[str, str]] = []
        self.fail_reply_for: set[str] = set()
        self.quota = (0, 1000)

    def me(self):
        return "42", self.username

    def keyword_search(self, query, **kwargs):
        if query == "boom":
            raise ThreadsAPIError(500, "search broke")
        return self.results.get(query, [])

    def reply_quota(self):
        return self.quota

    def reply(self, reply_to_id, text):
        if reply_to_id in self.fail_reply_for:
            raise ThreadsAPIError(400, "replies restricted")
        self.replies.append((reply_to_id, text))
        return f"r{reply_to_id}"

    def permalink(self, media_id):
        return f"https://www.threads.com/@me/post/{media_id}"


class FakeDrafter:
    """Maps post id -> Assessment (or None for a refusal)."""

    def __init__(self, by_id: dict[str, Assessment | None] | None = None):
        self.by_id = by_id or {}
        self.calls: list[str] = []

    def assess(self, post, query=""):
        self.calls.append(post.id)
        return self.by_id.get(post.id, support())


def support(link: bool = True) -> Assessment:
    return Assessment(
        category=Category.SEEKING_SUPPORT,
        should_reply=True,
        rationale="Asking whether it gets easier.",
        reply_body="Crying in the shower three weeks in is so common, and so lonely. It does get lighter.",
        link_line="I made a little site with a daily letter from another mom, if it helps: [LINK]" if link else "",
    )


def crisis() -> Assessment:
    return Assessment(category=Category.CRISIS, should_reply=False, rationale="Mentions not wanting to be here.",
                      reply_body="", link_line="")
