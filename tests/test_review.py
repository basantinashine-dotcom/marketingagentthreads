from outreach.review import review

from .conftest import NOW, make_post
from .fakes import support


def _draft(store, pid, username, link=True):
    a = support(link)
    store.add_post(make_post(pid, username), "postpartum", NOW)
    store.save_draft(pid, status="drafted", category=a.category.value, rationale=a.rationale,
                     body=a.reply_body, link_line=a.link_line)


def run(cfg, store, answers, edit=lambda t: t):
    answers = iter(answers)
    out = []
    counts = review(cfg, store, now_fn=lambda: NOW, prompt=lambda p: next(answers), out=out.append, edit=edit)
    return counts, "\n".join(out)


def test_approve_with_link_then_budget_limits_links(cfg, store):
    for i, user in enumerate(["a", "b", "c", "d"]):
        _draft(store, str(i), user)
    counts, output = run(cfg, store, ["a", "a", "a", "a"])
    assert counts["approved"] == 4
    approved = store.with_status("approved")
    # The 2-in-3 cap, applied as each reply is approved.
    assert [r.includes_link for r in approved] == [True, False, True, False]
    for r in approved:
        assert ("lettersfornewmom.com" in r.final_text) == r.includes_link
        assert "I made" in r.final_text or not r.includes_link


def test_skip_edit_toggle_and_quit(cfg, store):
    _draft(store, "1", "a")
    _draft(store, "2", "b")
    _draft(store, "3", "c")
    counts, output = run(
        cfg, store,
        ["s",            # skip post 1
         "l", "e", "a",  # post 2: link off, edit, approve
         "q"],           # quit at post 3
        edit=lambda t: t + " 💛",
    )
    assert counts == {"approved": 1, "skipped": 1, "left": 1}
    [row] = store.with_status("approved")
    assert row.final_text.endswith("💛") and not row.includes_link
    assert [r.id for r in store.with_status("drafted")] == ["3"]


def test_cannot_approve_invalid_reply(cfg, store):
    _draft(store, "1", "a")
    counts, output = run(cfg, store, ["e", "a", "s"], edit=lambda t: "visit www.elsewhere.com")
    assert "Can't approve" in output and counts["approved"] == 0
