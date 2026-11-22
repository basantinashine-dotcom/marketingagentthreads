"""The growth agent, run end to end against a scripted Claude and a fake Threads.

ScriptedClaude fakes the Messages API at the HTTP level, so the SDK's real tool
runner drives the loop: it sends each request, runs our tools, and sends the
results back. Nothing touches the network.
"""

import json
from dataclasses import replace

import anthropic
import httpx
import httpx2  # the Anthropic SDK's HTTP library

from outreach.agent import GrowthAgent, build_system_prompt
from outreach.policy import growth_problems
from outreach.threads_api import Comment, ThreadsAPIError, ThreadsClient

from .conftest import NOW, make_post
from .fakes import FakeDrafter, FakeThreads, crisis, support


class ScriptedClaude:
    """Returns one scripted assistant turn per request and records every request."""

    def __init__(self, turns, stop_reason=None):
        self.turns = list(turns)
        self.stop_reason = stop_reason
        self.requests: list[dict] = []
        self.headers: list[httpx2.Headers] = []

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        content = self.turns.pop(0) if self.turns else [{"type": "tool_use", "id": f"tu_{len(self.requests)}",
                                                         "name": "get_limits", "input": {}}]
        stop = self.stop_reason or ("tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn")
        return httpx2.Response(200, json={
            "id": f"msg_{len(self.requests)}", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        })

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(self._handle))
        )

    def tool_results(self, request_index: int) -> dict[str, dict]:
        """tool_use id -> tool_result block, from the last user message of one request."""
        last = self.requests[request_index]["messages"][-1]
        return {b["tool_use_id"]: b for b in last["content"] if b.get("type") == "tool_result"}


def use(tool_id, name, **inputs):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inputs}


def result_text(block) -> str:
    content = block["content"]
    return content if isinstance(content, str) else " ".join(c.get("text", "") for c in content)


def comment(cid, user, text, replied_to="p1"):
    base = make_post(cid, user, text=text)
    return Comment(**base.__dict__, replied_to_id=replied_to)


def threads_with_comments() -> FakeThreads:
    threads = FakeThreads(me="me")
    threads.posts = [make_post("p1", "me", text="What's one thing nobody told you about week two?")]
    threads.comments = {"p1": [
        comment("c1", "alice", "That you'd cry at diaper commercials. Every. Single. One."),
        comment("c2", "bob", "Honestly I don't see the point of anything anymore."),
    ]}
    return threads


def agent_for(cfg, store, threads, claude, drafter=None):
    return GrowthAgent(cfg, threads, store, drafter or FakeDrafter({"c2": crisis()}),
                       now_fn=lambda: NOW, client=claude.client(), log=lambda _: None)


GOOD_POST = "What's one small thing that made this week easier?"


def test_a_run_looks_first_queues_within_the_rules_and_publishes_nothing(cfg, store):
    threads = threads_with_comments()
    claude = ScriptedClaude([
        [use("t1", "read_notes"), use("t2", "get_account_stats"), use("t3", "list_my_recent_posts", days=7)],
        [use("t4", "list_new_comments")],
        [use("t5", "draft_reply", comment_id="c1"), use("t6", "draft_reply", comment_id="c2"),
         use("t7", "propose_post", body="Comment YES if week two broke you too", theme="bait", why="replies")],
        [use("t8", "propose_post", body=GOOD_POST, theme="small wins", why="Questions got the most replies."),
         use("t9", "save_note", note="Question posts got 3x the replies of statements (2 each, too few to be sure).")],
        [{"type": "text", "text": "Queued one reply and one question post."}],
    ])
    run = agent_for(cfg, store, threads, claude).run()

    # It looked before acting, and its notes and the follower count were saved for next time.
    first = claude.tool_results(1)
    assert "No notes yet" in result_text(first["t1"])
    assert "Followers now: 1,200" in result_text(first["t2"])
    assert "views 900" in result_text(first["t3"])
    assert store.follower_history() == [(NOW.date().isoformat(), 1200)]
    assert [text for _, text in store.recent_notes()] == [run.notes[0]]

    # Comments arrive wrapped as untrusted content.
    assert '<comment id="c1" author="@alice">' in result_text(claude.tool_results(2)["t4"])

    # The friendly comment got a draft; the worrying one went to the user, not to Claude.
    third = claude.tool_results(3)
    assert "Drafted and queued" in result_text(third["t5"])
    assert "never gets an automatic reply" in result_text(third["t6"])
    assert run.needs_you == [("crisis", "bob", "https://www.threads.com/@bob/post/c2")]

    # Engagement bait was rejected in code, and Claude was told why.
    assert third["t7"].get("is_error") is True
    assert "engagement bait" in result_text(third["t7"])

    # Two items are waiting for review; nothing was published.
    assert run.queued[0] == "c1" and run.queued[1].startswith("post-")
    assert {r.id for r in store.with_status("drafted")} == set(run.queued)
    assert threads.published == [] and threads.replies == []
    assert run.summary == "Queued one reply and one question post."
    assert run.stop_reason == "end_turn"


def test_every_request_uses_the_goal_fallbacks_and_only_safe_tools(cfg, store):
    claude = ScriptedClaude([[{"type": "text", "text": "Nothing to do today."}]])
    agent_for(cfg, store, FakeThreads(), claude).run()

    request = claude.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in claude.headers[0]["anthropic-beta"]
    assert cfg.goal.statement in json.dumps(request["system"])
    names = {tool["name"] for tool in request["tools"]}
    assert names == {"read_notes", "get_account_stats", "list_my_recent_posts", "list_new_comments",
                     "draft_reply", "propose_post", "save_note", "get_limits"}
    # No tool can publish, follow, like or message anyone.
    assert not any(word in name for name in names for word in ("publish", "follow", "like", "message", "dm"))


def test_the_run_budget_is_enforced_in_code(cfg, store):
    cfg = replace(cfg, goal=replace(cfg.goal, max_proposals_per_run=1))
    claude = ScriptedClaude([
        [use("t1", "propose_post", body=GOOD_POST, theme="a", why="b"),
         use("t2", "propose_post", body="Slow days still count.", theme="a", why="b")],
        [{"type": "text", "text": "Done."}],
    ])
    run = agent_for(cfg, store, FakeThreads(), claude).run()
    results = claude.tool_results(1)
    assert "Queued for review" in result_text(results["t1"])
    assert results["t2"]["is_error"] is True and "budget is used up" in result_text(results["t2"])
    assert len(run.queued) == 1


def test_the_daily_post_cap_counts_drafts_already_waiting(cfg, store):
    for i in range(cfg.limits.max_new_posts_per_day):
        store.add_new_post(f"Waiting post {i}", "", "theme", NOW)
    claude = ScriptedClaude([
        [use("t1", "propose_post", body=GOOD_POST, theme="a", why="b")],
        [{"type": "text", "text": "Done."}],
    ])
    run = agent_for(cfg, store, FakeThreads(), claude).run()
    assert "daily cap" in result_text(claude.tool_results(1)["t1"])
    assert run.queued == []


def test_links_and_mentions_are_rejected_even_if_a_comment_asked_for_them(cfg, store):
    claude = ScriptedClaude([
        [use("t1", "propose_post", body="Great tips at https://spam.example and @someone", theme="a", why="b")],
        [{"type": "text", "text": "Done."}],
    ])
    agent_for(cfg, store, FakeThreads(), claude).run()
    message = result_text(claude.tool_results(1)["t1"])
    assert "URL or domain" in message and "@mentions" in message
    assert store.with_status("drafted") == []


def test_a_run_always_ends_at_the_turn_limit(cfg, store):
    cfg = replace(cfg, goal=replace(cfg.goal, max_turns=3))
    claude = ScriptedClaude([])  # calls get_limits forever
    run = agent_for(cfg, store, FakeThreads(), claude).run()
    assert len(claude.requests) == 3
    assert "Stopped at the limit of 3 turns" in run.summary


def test_missing_insights_permission_is_reported_not_fatal(cfg, store):
    threads = FakeThreads()
    threads.insights_error = ThreadsAPIError(403, "permission missing", 10)
    claude = ScriptedClaude([[use("t1", "get_account_stats")], [{"type": "text", "text": "Worked without stats."}]])
    run = agent_for(cfg, store, threads, claude).run()
    result = claude.tool_results(1)["t1"]
    assert result["is_error"] is True and "threads_manage_insights" in result_text(result)
    assert run.summary == "Worked without stats."


def test_a_refusal_ends_the_run_cleanly(cfg, store):
    claude = ScriptedClaude([[{"type": "text", "text": ""}]], stop_reason="refusal")
    run = agent_for(cfg, store, FakeThreads(), claude).run()
    assert run.stop_reason == "refusal" and "declined" in run.summary


def test_growth_guardrails():
    assert growth_problems("Follow me for more letters like this")
    assert growth_problems("Like if this is you")
    assert growth_problems("Tag a mom who needs to hear this")
    assert growth_problems("Unpopular opinion: naps are overrated")
    assert growth_problems(GOOD_POST) == []
    assert growth_problems("Slow days still count.") == []


def test_system_prompt_carries_the_goal_and_the_users_own_limits(cfg):
    prompt = build_system_prompt(cfg)
    assert cfg.goal.statement in prompt
    for item in cfg.goal.never:
        assert item in prompt
    assert f"at most {cfg.goal.max_proposals_per_run} items" in prompt


def test_insights_are_read_from_both_response_shapes():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/me/threads_insights"):
            if request.url.params["metric"] == "followers_count":
                return httpx.Response(200, json={"data": [{"name": "followers_count", "total_value": {"value": 1234}}]})
            return httpx.Response(200, json={"data": [
                {"name": "views", "period": "day", "values": [{"value": 100}, {"value": 250}]},
                {"name": "likes", "period": "day", "total_value": {"value": 31}},
            ]})
        if request.url.path.endswith("/m1/insights"):
            return httpx.Response(200, json={"data": [{"name": "replies", "values": [{"value": 7}]}]})
        raise AssertionError(request.url.path)

    client = ThreadsClient("tok", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    assert client.followers() == 1234
    assert client.account_insights(since=1, until=2) == {"views": 350, "likes": 31}
    assert client.post_insights("m1") == {"replies": 7}


def test_inbox_still_drafts_through_the_shared_helper(cfg, store):
    from outreach.pipeline import inbox

    threads = threads_with_comments()
    result = inbox(cfg, threads, store, FakeDrafter({"c1": support(link=False), "c2": crisis()}), NOW)
    assert result.drafted == 1
    assert result.needs_you == [("crisis", "bob", "https://www.threads.com/@bob/post/c2")]
