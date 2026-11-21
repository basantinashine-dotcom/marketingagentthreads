"""A small client for the parts of the Threads Graph API this tool uses.

Endpoints (https://graph.threads.net/v1.0):
  GET  /me                               id, username of the brand account
  GET  /keyword_search                   public posts by keyword or topic tag
  POST /me/threads  (reply_to_id=...)    create a reply container
  GET  /{container_id}?fields=status     wait for the container to be ready
  POST /me/threads_publish               publish the container
  GET  /me/threads_publishing_limit      reply quota usage
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://graph.threads.net/v1.0"
SEARCH_FIELDS = "id,text,media_type,permalink,timestamp,username"


COMMENT_FIELDS = "id,text,username,permalink,timestamp,replied_to,hide_status"


@dataclass(frozen=True)
class ThreadsPost:
    id: str
    text: str
    username: str
    permalink: str
    timestamp: str
    media_type: str = "TEXT"


@dataclass(frozen=True)
class Comment(ThreadsPost):
    replied_to_id: str = ""
    hidden: bool = False


def _post_from(item: dict[str, Any]) -> ThreadsPost:
    return ThreadsPost(
        id=item["id"],
        text=item.get("text") or "",
        username=item.get("username", ""),
        permalink=item.get("permalink", ""),
        timestamp=item.get("timestamp", ""),
        media_type=item.get("media_type", "TEXT"),
    )


class ThreadsAPIError(Exception):
    def __init__(self, status: int, message: str, code: int | None = None):
        super().__init__(f"Threads API {status}: {message}" + (f" (code {code})" if code else ""))
        self.status = status
        self.code = code


class ThreadsClient:
    def __init__(
        self,
        access_token: str,
        http: httpx.Client | None = None,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not access_token:
            raise ValueError("A Threads access token is required.")
        # Sent as a header, not a URL parameter, so the token stays out of logs
        # and a credential-injecting proxy can supply it.
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._http = http or httpx.Client(timeout=30.0)
        self._base = base_url.rstrip("/")
        self._sleep = sleep

    # -- low level -----------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: ThreadsAPIError | None = None
        for attempt in range(3):
            resp = self._http.get(f"{self._base}{path}", params=params, headers=self._headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = _error_from(resp)
                self._sleep(2.0 * (attempt + 1))
                continue
            return _json_or_raise(resp)
        assert last_error is not None
        raise last_error

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        # POSTs are not retried: a retried publish could post the reply twice.
        resp = self._http.post(f"{self._base}{path}", data=data, headers=self._headers)
        return _json_or_raise(resp)

    # -- endpoints -----------------------------------------------------------

    def me(self) -> tuple[str, str]:
        body = self._get("/me", {"fields": "id,username"})
        return body["id"], body.get("username", "")

    def keyword_search(
        self,
        query: str,
        *,
        search_type: str = "RECENT",
        tag: bool = False,
        since: int | None = None,
        limit: int = 25,
    ) -> list[ThreadsPost]:
        params: dict[str, Any] = {
            "q": query,
            "search_type": search_type,
            "search_mode": "TAG" if tag else "KEYWORD",
            "fields": SEARCH_FIELDS,
            "limit": limit,
        }
        if since is not None:
            params["since"] = since
        body = self._get("/keyword_search", params)
        posts = []
        for item in body.get("data", []):
            if not item.get("id") or not item.get("text"):
                continue  # media-only posts have nothing to respond to
            posts.append(
                ThreadsPost(
                    id=item["id"],
                    text=item["text"],
                    username=item.get("username", ""),
                    permalink=item.get("permalink", ""),
                    timestamp=item.get("timestamp", ""),
                    media_type=item.get("media_type", "TEXT"),
                )
            )
        return posts

    def reply_quota(self) -> tuple[int, int]:
        """Returns (replies published in the last 24h, quota total)."""
        body = self._get("/me/threads_publishing_limit", {"fields": "reply_quota_usage,reply_config"})
        data = (body.get("data") or [{}])[0]
        used = int(data.get("reply_quota_usage", 0))
        total = int((data.get("reply_config") or {}).get("quota_total", 1000))
        return used, total

    def my_posts(self, *, since: int | None = None, limit: int = 25) -> list[ThreadsPost]:
        """Your own top-level posts, newest first (threads_basic)."""
        params: dict[str, Any] = {"fields": SEARCH_FIELDS, "limit": limit}
        if since is not None:
            params["since"] = since
        body = self._get("/me/threads", params)
        return [_post_from(item) for item in body.get("data", []) if item.get("id")]

    def conversation(self, media_id: str, *, limit: int = 100) -> list[Comment]:
        """Every reply under one of your posts, nested ones included (threads_read_replies)."""
        body = self._get(
            f"/{media_id}/conversation",
            {"fields": COMMENT_FIELDS, "reverse": "false", "limit": limit},
        )
        comments = []
        for item in body.get("data", []):
            if not item.get("id"):
                continue
            post = _post_from(item)
            comments.append(
                Comment(
                    **post.__dict__,
                    replied_to_id=(item.get("replied_to") or {}).get("id", ""),
                    hidden=item.get("hide_status") not in (None, "NOT_HUSHED", "UNHUSHED"),
                )
            )
        return comments

    def reply(self, reply_to_id: str, text: str, *, max_wait_s: float = 30.0) -> str:
        """Publishes a text reply. Returns the new post's media id."""
        return self.publish(text, reply_to_id=reply_to_id, max_wait_s=max_wait_s)

    def publish(self, text: str, *, reply_to_id: str | None = None, max_wait_s: float = 30.0) -> str:
        """Publishes a text post, or a reply when reply_to_id is given. Returns its media id."""
        data: dict[str, Any] = {"media_type": "TEXT", "text": text}
        if reply_to_id:
            data["reply_to_id"] = reply_to_id
        container = self._post("/me/threads", data)
        creation_id = container["id"]
        self._wait_until_ready(creation_id, max_wait_s)
        published = self._post("/me/threads_publish", {"creation_id": creation_id})
        return published["id"]

    def permalink(self, media_id: str) -> str:
        return self._get(f"/{media_id}", {"fields": "permalink"}).get("permalink", "")

    def _wait_until_ready(self, creation_id: str, max_wait_s: float) -> None:
        waited = 0.0
        while True:
            try:
                body = self._get(f"/{creation_id}", {"fields": "status,error_message"})
            except ThreadsAPIError as exc:
                # A new container can briefly read as "does not exist" (code 24).
                if exc.code != 24 or waited >= max_wait_s:
                    raise
                body = {"status": "IN_PROGRESS"}
            status = body.get("status", "FINISHED")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED", "PUBLISHED"):
                raise ThreadsAPIError(0, f"container {creation_id} is {status}: {body.get('error_message', '')}")
            if waited >= max_wait_s:
                raise ThreadsAPIError(0, f"container {creation_id} not ready after {max_wait_s:.0f}s")
            self._sleep(2.0)
            waited += 2.0


def _error_from(resp: httpx.Response) -> ThreadsAPIError:
    try:
        err = resp.json().get("error", {})
        return ThreadsAPIError(resp.status_code, err.get("message", resp.text[:200]), err.get("code"))
    except ValueError:
        return ThreadsAPIError(resp.status_code, resp.text[:200])


def _json_or_raise(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise _error_from(resp)
    body = resp.json()
    if isinstance(body, dict) and "error" in body:
        raise _error_from(resp)
    return body
