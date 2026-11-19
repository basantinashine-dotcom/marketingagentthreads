import httpx
import pytest

from outreach.threads_api import ThreadsAPIError, ThreadsClient


def client_with(handler):
    return ThreadsClient("tok", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def test_keyword_search_params_and_parsing():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(dict(request.url.params))
        assert request.url.path == "/v1.0/keyword_search"
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"data": [
            {"id": "1", "text": "hello", "username": "a", "permalink": "p", "timestamp": "t"},
            {"id": "2", "media_type": "IMAGE"},  # no text: dropped
        ]})

    posts = client_with(handler).keyword_search("postpartum", tag=True, since=123, limit=10)
    assert [p.id for p in posts] == ["1"]
    assert seen["q"] == "postpartum" and seen["search_mode"] == "TAG" and seen["since"] == "123"
    assert seen["search_type"] == "RECENT" and "access_token" not in seen


def test_reply_creates_waits_and_publishes():
    calls = []
    statuses = iter(["IN_PROGRESS", "FINISHED"])

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/me/threads"):
            body = dict(httpx.QueryParams(request.content.decode()))
            assert body["reply_to_id"] == "123" and body["media_type"] == "TEXT" and body["text"] == "hi"
            return httpx.Response(200, json={"id": "c1"})
        if request.method == "GET" and request.url.path.endswith("/c1"):
            return httpx.Response(200, json={"status": next(statuses)})
        if request.url.path.endswith("/threads_publish"):
            assert dict(httpx.QueryParams(request.content.decode()))["creation_id"] == "c1"
            return httpx.Response(200, json={"id": "m1"})
        raise AssertionError(request.url)

    assert client_with(handler).reply("123", "hi") == "m1"
    assert calls[-1] == ("POST", "/v1.0/me/threads_publish")


def test_errors_raise_and_gets_retry_on_5xx():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(500, json={"error": {"message": "boom", "code": 1}})

    with pytest.raises(ThreadsAPIError, match="boom"):
        client_with(handler).me()
    assert len(attempts) == 3

    def bad_post(request):
        return httpx.Response(400, json={"error": {"message": "replies restricted", "code": 10}})

    with pytest.raises(ThreadsAPIError, match="replies restricted"):
        client_with(bad_post).reply("1", "hi")


def test_reply_quota():
    def handler(request):
        return httpx.Response(200, json={"data": [{"reply_quota_usage": 7, "reply_config": {"quota_total": 1000}}]})

    assert client_with(handler).reply_quota() == (7, 1000)
