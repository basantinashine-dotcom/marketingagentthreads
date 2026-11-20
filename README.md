# marketingagentthreads

Grows conversations about life on Threads, from your own account:

1. **New posts.** Claude drafts short posts in your voice that invite users to answer.
2. **Replies.** It collects comments on your recent posts and drafts a warm reply to each one.
3. **You approve.** Nothing is published until you approve it. Then it posts, paced and capped.

Some replies and posts mention [lettersfornewmom.com](https://lettersfornewmom.com), and always say you made it.

This works with **standard** Threads API access: no Meta App Review, no Tech Provider status.
(Searching strangers' posts, `outreach discover`, does need App Review and is optional.)

```
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

## Setup

### 1. Meta app

1. At [developers.facebook.com](https://developers.facebook.com/apps), create an app with the
   **Access the Threads API** use case.
2. In the use case, add `threads_content_publish`, `threads_read_replies` and `threads_manage_replies`.
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
  threads_api.py   Threads API: your posts, their comments, publish, reply, search
  drafter.py       Claude: replies to comments, new posts, replies to searched posts
  policy.py        hard rules: never-reply categories, link budget, validation, warnings
  pipeline.py      inbox, compose, discover, and posting with caps
  review.py        approval, interactive or one at a time
  store.py         SQLite: one row per draft, new -> drafted -> approved -> posted
  cli.py           the `outreach` command
```

Tests: `pytest` (no network; Threads and Claude are faked).
