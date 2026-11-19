from outreach import policy
from outreach.policy import Category

LINK = "https://lettersfornewmom.com/?utm_source=threads"


def test_never_reply_categories_are_skipped_even_if_model_drafts():
    for cat in (Category.CRISIS, Category.MEDICAL_EMERGENCY, Category.GRIEF_OR_LOSS,
                Category.COMMERCIAL_OR_SPAM, Category.NOT_RELEVANT):
        d = policy.decide(cat, True, "some reply")
        assert d.status == "skipped" and d.reason == f"category:{cat.value}"


def test_model_no_reply_and_empty_draft_are_skipped():
    assert policy.decide(Category.VENTING, False, "x").reason == "model:no_reply"
    assert policy.decide(Category.VENTING, True, "  ").reason == "model:empty_draft"
    assert policy.decide(Category.VENTING, True, "hugs").status == "drafted"


def test_compose_adds_link_only_when_allowed():
    line = "I made a little site with daily letters from other moms: [LINK]"
    with_link = policy.compose(Category.SEEKING_SUPPORT, "You're not alone.", line, LINK, True)
    assert with_link.endswith(LINK) and "[LINK]" not in with_link
    assert policy.compose(Category.SEEKING_SUPPORT, "You're not alone.", line, LINK, False) == "You're not alone."
    # medical questions never get the link, whatever the model drafted
    assert LINK not in policy.compose(Category.MEDICAL_QUESTION, "Ask your OB.", line, LINK, True)


def test_link_budget_two_in_three():
    ratio = 0.67
    links = total = 0
    pattern = []
    for _ in range(9):
        use = policy.link_budget_available(links, total, ratio)
        pattern.append(use)
        links += use
        total += 1
    assert links == 6 and total == 9
    assert policy.link_budget_available(0, 0, 0.0) is False


def test_validate_requires_disclosure_with_link():
    no_disclosure = f"So glad you said this.\n\nThere are daily letters here: {LINK}"
    assert any("saying it's yours" in e for e in policy.validate(no_disclosure, LINK))
    ok = f"So glad you said this.\n\nI made a little site with daily letters: {LINK}"
    assert policy.validate(ok, LINK) == []


def test_validate_blocks_foreign_urls_phones_mentions_and_length():
    assert policy.validate("see www.other.com", LINK)
    assert policy.validate("lettersfornewmom.com has letters", LINK)  # bare domain must use the tracked link
    assert policy.validate("call 1-800-555-0100", LINK)
    assert policy.validate("ask @someone", LINK)
    assert policy.validate("x" * 501, LINK)
    assert policy.validate("hi [LINK]", LINK)
    assert policy.validate("Six weeks in and you're still showing up. That counts.", LINK) == []


def test_warnings_flag_personal_claims_and_meds():
    assert "mentions your personal life; check it's true" in policy.warnings("When I had my baby it was hard")
    assert "mentions medication, supplements or dosing" in policy.warnings("try a magnesium supplement")
    assert policy.warnings("That sounds so heavy. You're doing more than you think.") == []
