# marketingagentthreads

Grows your Threads account from your own profile, with Claude doing the thinking and you approving
everything that goes out.

There are two ways to run it:

- **The growth agent** (`outreach agent`). You give Claude a goal, such as "grow my following among new
  mothers by starting genuine conversations". Each run, Claude decides what to look at (your follower
  count and recent numbers, how each recent post did, new comments, its own notes from earlier runs),
  then queues a few replies and new posts it thinks will help, and saves notes on what seems to work.
- **The step-by-step workflow** (`inbox`, `compose`). Code runs fixed steps: collect comments and draft
  a reply to each, or draft a batch of new posts.

Either way, nothing is published until you approve it, and posting is paced and capped in code.

Some replies and posts mention [lettersfornewmom.com](https://lettersfornewmom.com), and always say you made it.

This works with **standard** Threads API access: no Meta App Review, no Tech Provider status.
(Searching strangers' posts, `outreach discover`, does need App Review and is optional.)

```
outreach agent      Claude looks at your numbers and comments, then queues replies and posts toward your goal
outreach inbox      collect new comments on your posts and draft replies
outreach compose    draft new posts
outreach review     approve, edit or skip each draft
outreach post       publish what you approved
```

## Rules enforced in code

| Situation | What happens |
|---|---|
| A friendly, venting, struggling or celebrating comment | Drafts a short reply. Offers the link when it fits. |
| A medical question | Points them to their OB, midwife or doctor. No link, no medical advice. |
| Signs of a mental-health crisis, a medical emergency, or loss | Never replied to automatically. `inbox` tells you about it so you can decide yourself. |
| Spam or bots | Never replied to. |
| A comment you already answered, in the app or here | Skipped. |
| Comments between other people under your post | Skipped. |

- **Link share:** in at most 2 of every 3 published posts and replies (rolling 7 days).
- **Disclosure:** anything with the link must say you made the site ("I made…"), or it can't be approved.
- **Allowed content:** no other URLs or domains, no phone numbers, no @mentions, 500 characters max.
- **Pacing:** at most 10 replies and 3 new posts a day, at least 5 minutes apart.
- **Facts about you:** only what you list under `[voice]` in `config.toml`. The model never invents
  children, birth stories or feelings, and review flags any personal claim for you to check.

## The growth agent

A workflow runs steps that code decides in advance. An agent decides its own next step. Here Claude
gets a goal and eight tools, and chooses which to call, in what order, and when it's done:

| Tool | What it does |
|---|---|
| `read_notes` | Reads the notes it saved in earlier runs |
| `get_account_stats` | Follower count (also saved each run, so it can see the trend) and the last 7 days' views, likes, replies, reposts, quotes |
| `list_my_recent_posts` | Your recent posts with each one's views, likes, replies, reposts, quotes |
| `list_new_comments` | New comments waiting for an answer, marked as untrusted text |
| `draft_reply` | Drafts a reply to one comment through the same classifier and never-reply rules as `inbox` |
| `propose_post` | Queues a new post for your review |
| `save_note` | Saves what it learned, with the evidence, for the next run |
| `get_limits` | What's left of this run's budget and today's caps |

A goal like "grow followers" invites shortcuts: engagement bait, follow-for-follow, hot takes, mass
replies. So the limits live in code, not only in the prompt:

- **No tool publishes, follows, likes or messages anyone.** The agent can only queue drafts.
- **A budget per run:** at most `goal.max_proposals_per_run` items (default 5), within the daily caps,
  counting drafts already waiting for you.
- **Growth guardrails:** proposed posts that ask for follows, trade likes/comments/tags, or open with
  rage bait ("unpopular opinion", "fight me") are rejected, and Claude is told why. So are links,
  @mentions, phone numbers and posts over 500 characters.
- **The same never-reply rules:** crisis, grief, medical emergencies and spam never get a reply; you're
  told about the sensitive ones.
- **A run always ends:** at most `goal.max_turns` Claude turns (default 20).

Set the goal, the audience, and anything it must never do under `[goal]` in `config.toml`. The agent
needs one more permission: add `threads_manage_insights` to your Meta app and regenerate the token
(`outreach check` tells you if it's missing; without it the agent still works, minus the numbers).

```bash
outreach agent            # look, decide, queue a few items, save notes, then summarize
outreach review           # approve, edit or skip what it queued
outreach post
```

What to expect: followers move slowly and for many reasons, so give it a few weeks before judging.
Claude doesn't train on your results; it learns by reading its own notes and your numbers each run,
so read its notes now and then (`sqlite3 outreach.db "select * from notes"`) and delete any that are
wrong. Each run is one Claude call per turn, up to `goal.max_turns`, so it costs more than a single
`inbox` draft; check your usage after the first few runs.

## Setup

### 1. Meta app

1. At [developers.facebook.com](https://developers.facebook.com/apps), create an app with the
   **Access the Threads API** use case.
2. In the use case, add `threads_content_publish`, `threads_read_replies` and `threads_manage_replies`,
   plus `threads_manage_insights` for the growth agent.
3. **App roles → Roles**: add your Threads account as a **Threads Tester**, then accept the invite in
   Threads (Settings → Account → Website permissions → Invites).
4. Use the **User Token Generator** in the use case settings to generate a token for your account.
   It lasts 60 days.

### 2. Install and configure

```bash
python -m venv .venv && source .venv/bin/activate     # Windows Git Bash: source .venv/Scripts/activate
pip install -e ".[dev]"
cp config.example.toml config.toml    # edit [voice]: a few true facts about you, and how you write
export THREADS_ACCESS_TOKEN=...       # never commit this
export ANTHROPIC_API_KEY=...          # for inbox/compose drafting
outreach check
```

`check` confirms the token can read your posts and the comments on them.

## Daily use

```bash
outreach agent            # or, step by step:
outreach inbox            # new comments -> drafted replies (and alerts for anything sensitive)
outreach compose          # 3 new post ideas
outreach review           # a / e / l / s / q for each draft
outreach post --dry-run   # see exactly what will go out
outreach post
```

Prefer approving one at a time? `outreach list`, then `outreach approve ID` (add `--no-link` to drop
the link) or `outreach skip ID`. `outreach draft` saves a draft you wrote yourself.

Every link carries `utm_source=threads&utm_medium=social&utm_campaign=replies`, so visits show up
under that campaign in your site analytics. `outreach stats` shows counts and links to what was posted.

## Cost

Each draft is one Claude call (Claude Opus 5.5, medium effort), roughly 2–4¢.

## Code

```
outreach/
  agent.py         the growth agent: goal, tools, budgets, and the Claude tool loop
  threads_api.py   Threads API: your posts, their comments, insights, publish, reply, search
  drafter.py       Claude: replies to comments, new posts, replies to searched posts
  policy.py        hard rules: never-reply categories, link budget, validation, warnings
  pipeline.py      inbox, compose, discover, and posting with caps
  review.py        approval, interactive or one at a time
  store.py         SQLite: one row per draft (new -> drafted -> approved -> posted), plus the agent's
                   notes and daily follower counts
  cli.py           the `outreach` command
```

Tests: `pytest` (no network; Threads and Claude are faked). The agent tests script Claude's turns at
the HTTP level, so the SDK's real tool runner drives the loop.
