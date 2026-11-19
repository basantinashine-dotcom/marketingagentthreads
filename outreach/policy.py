"""Deterministic rules applied around the model.

The model proposes; this module decides what is allowed. Who never gets a
reply, when the link may appear, and what a reply may contain are enforced
here in code rather than trusted to the prompt.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

MAX_POST_CHARS = 500  # Threads' limit for a post's text
LINK_PLACEHOLDER = "[LINK]"


class Category(StrEnum):
    SEEKING_SUPPORT = "seeking_support"
    VENTING = "venting"
    SHARING_EXPERIENCE = "sharing_experience"
    CELEBRATING = "celebrating"
    MEDICAL_QUESTION = "medical_question"
    CRISIS = "crisis"
    MEDICAL_EMERGENCY = "medical_emergency"
    GRIEF_OR_LOSS = "grief_or_loss"
    COMMERCIAL_OR_SPAM = "commercial_or_spam"
    NOT_RELEVANT = "not_relevant"


# Categories where mentioning the site can be appropriate.
LINK_OK = frozenset(
    {Category.SEEKING_SUPPORT, Category.VENTING, Category.SHARING_EXPERIENCE, Category.CELEBRATING}
)
# Never replied to, whatever the model drafts.
NEVER_REPLY = frozenset(
    {
        Category.CRISIS,
        Category.MEDICAL_EMERGENCY,
        Category.GRIEF_OR_LOSS,
        Category.COMMERCIAL_OR_SPAM,
        Category.NOT_RELEVANT,
    }
)


# Never replied to, but a comment like this on your own post is shown to you,
# in case you want to reach out yourself.
NEEDS_YOU = frozenset({Category.CRISIS, Category.MEDICAL_EMERGENCY, Category.GRIEF_OR_LOSS})


@dataclass(frozen=True)
class Decision:
    status: str  # "drafted" or "skipped"
    reason: str = ""


def decide(category: Category, should_reply: bool, body: str) -> Decision:
    if category in NEVER_REPLY:
        return Decision("skipped", f"category:{category.value}")
    if not should_reply:
        return Decision("skipped", "model:no_reply")
    if not body.strip():
        return Decision("skipped", "model:empty_draft")
    return Decision("drafted")


def link_allowed(category: Category | None, link_line: str, *, kind: str = "search") -> bool:
    if LINK_PLACEHOLDER not in link_line:
        return False
    return kind == "post" or category in LINK_OK


def compose(
    category: Category | None,
    body: str,
    link_line: str,
    link_url: str,
    include_link: bool,
    *,
    kind: str = "search",
) -> str:
    body = body.strip()
    if include_link and link_allowed(category, link_line, kind=kind):
        return f"{body}\n\n{link_line.strip().replace(LINK_PLACEHOLDER, link_url)}"
    return body


def link_budget_available(with_link: int, total: int, max_ratio: float) -> bool:
    """Whether one more linked reply keeps links at or under max_ratio of replies.

    The first reply may carry a link; after that, links are earned by replies
    that stand on their own.
    """
    if max_ratio <= 0:
        return False
    allowed = max(1, math.floor(max_ratio * (total + 1)))
    return with_link + 1 <= allowed


# -- validation ---------------------------------------------------------------

_URLISH = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|net|org|co|io|ly|app|me|link)\b", re.I)
_PHONEISH = re.compile(r"\d[\d\-\s().]{6,}\d")
_MENTION = re.compile(r"(?:^|\s)@\w")
# Recommending your own site without saying it's yours is misleading (and, in
# the US, an undisclosed endorsement). A linked reply must say you made it.
_DISCLOSURE = re.compile(
    r"\bI (?:made|built|created|started|run|write)\b|\bmy (?:own )?(?:site|project|little|website)\b",
    re.I,
)


def validate(text: str, link_url: str) -> list[str]:
    """Hard errors. A reply with any of these cannot be approved or posted."""
    errors = []
    if not text.strip():
        errors.append("reply is empty")
    if len(text) > MAX_POST_CHARS:
        errors.append(f"reply is {len(text)} characters; Threads allows {MAX_POST_CHARS}")
    if LINK_PLACEHOLDER in text:
        errors.append("reply still contains the [LINK] placeholder")
    if link_url in text and not _DISCLOSURE.search(text):
        errors.append("reply links your site without saying it's yours (e.g. \"I made...\")")

    rest = text.replace(link_url, " ")
    if _URLISH.search(rest):
        errors.append("reply contains a URL or domain other than your tracked link")
    if _PHONEISH.search(rest):
        errors.append("reply contains a phone number")
    if _MENTION.search(rest):
        errors.append("reply @mentions someone")
    return errors


_WARNINGS: list[tuple[str, re.Pattern[str]]] = [
    (
        "mentions your personal life; check it's true",
        re.compile(
            r"\bmy (?:own )?(?:baby|babies|son|daughter|kids?|little one|husband|partner|wife|"
            r"birth|labou?r|c-section|delivery|pregnancy|postpartum)\b|\bwhen I (?:had|was|gave birth)\b|"
            r"\bI remember\b|\bI(?:'ve| have) been there\b|\bI went through\b",
            re.I,
        ),
    ),
    (
        "mentions medication, supplements or dosing",
        re.compile(r"\b\d+\s?mg\b|\bdosage\b|\bdose\b|\bsupplements?\b|\bmedication\b|\bmeds\b|\bibuprofen\b", re.I),
    ),
    (
        "may minimize what they're feeling",
        re.compile(
            r"\bat least\b|\benjoy every\b|\bgoes by (?:so )?fast\b|\bsleep when the baby sleeps\b|\bjust relax\b",
            re.I,
        ),
    ),
    (
        "sounds like marketing",
        re.compile(
            r"\bsign up\b|\bsubscribe\b|\bfree trial\b|\bdiscount\b|\bpromo\b|\blink in bio\b|"
            r"\bcheck (?:it|this) out\b|\bdm me\b",
            re.I,
        ),
    ),
    ("uses a hashtag", re.compile(r"(?:^|\s)#\w")),
]


def warnings(text: str) -> list[str]:
    """Soft flags shown to the reviewer. They don't block approval."""
    return [label for label, pattern in _WARNINGS if pattern.search(text)]
