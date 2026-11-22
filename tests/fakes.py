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
        self.posts = []
        self.comments = {}
        self.published = []

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

    posts: list = []
    comments: dict = {}
    published: list = []

    def my_posts(self, since=None, limit=25):
        return list(self.posts)

    def conversation(self, media_id):
        if media_id == "broken":
            raise ThreadsAPIError(403, "no permission", 10)
        return list(self.comments.get(media_id, []))

    def publish(self, text, reply_to_id=None):
        if reply_to_id:
            return self.reply(reply_to_id, text)
        self.published.append(text)
        return f"p{len(self.published)}"

    def permalink(self, media_id):
        return f"https://www.threads.com/@me/post/{media_id}"

    follower_count = 1200
    insights_error = None

    def followers(self):
        if self.insights_error:
            raise self.insights_error
        return self.follower_count

    def account_insights(self, since=None, until=None):
        if self.insights_error:
            raise self.insights_error
        return {"views": 8400, "likes": 310, "replies": 95, "reposts": 12, "quotes": 3}

    def post_insights(self, media_id):
        return {"views": 900, "likes": 40, "replies": 12, "reposts": 1, "quotes": 0, "shares": 2}


class FakeDrafter:
    """Maps post id -> Assessment (or None for a refusal)."""

    def __init__(self, by_id: dict[str, Assessment | None] | None = None):
        self.by_id = by_id or {}
        self.calls: list[str] = []

    def assess(self, post, query="", context=""):
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
