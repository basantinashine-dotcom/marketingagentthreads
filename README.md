# marketingagentthreads

Finds postpartum conversations on Threads, drafts a short reply in your voice
with Claude, and posts the ones you approve from your own Threads account,
sometimes with a link to [lettersfornewmom.com](https://lettersfornewmom.com).

Nothing is ever posted without your approval.

```
outreach discover   search Threads and draft replies
outreach review     approve, edit or skip each draft
outreach post       publish what you approved, paced and capped
```

## What it will and won't do

| Situation | What happens |
|---|---|
| Someone asking for support, venting, sharing, celebrating | Drafts a reply. Offers the link when it fits. |
| A medical question | Drafts a reply that points them to their OB, midwife or doctor. No link, no medical advice. |
| Signs of a mental-health crisis or a medical emergency | Never replied to. |
| Pregnancy or infant loss, ads/coaches/giveaways, off-topic | Never replied to. |
| Someone you've replied to in the last 30 days | Skipped. |
| Your own posts, posts older than 36h, one-word posts | Skipped. |

Rules enforced in code, whatever the model writes:
- **Link share:** at most 2 in 3 replies over a rolling 7 days carry the link (`max_link_ratio`).
- **Disclosure:** a reply that links the site must say you made it ("I made…"). A linked reply without that can't be approved.
- **Allowed content:** no other URLs or domains, no phone numbers, no @mentions, 500 characters max.
- **Pacing:** at most 10 replies a day, at least 5 minutes apart. Meta's own reply quota is checked before posting.
- **Checks shown in review:** any mention of your personal life (so you confirm it's true), medication, minimizing phrases ("at least…") or marketing language.
- **Retention:** post text is wiped from the local database after 30 days (`outreach purge`).

## Setup

### 1. Meta app (one time)

1. Go to [developers.facebook.com](https://developers.facebook.com/apps), **Create app**, and pick the
   **Access the Threads API** use case.
2. Under the use case's permissions, add `threads_basic`, `threads_content_publish`,
   `threads_manage_replies` and `threads_keyword_search`.
3. **App roles → Roles**: add your Threads account as a **Threads Tester**, then accept the
   invite in Threads (Settings → Account → Website permissions → Invites).
4. Generate a user token for your account in the use case settings, then exchange it for a
   long-lived (60-day) token:

   ```bash
   curl "https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret=APP_SECRET&access_token=SHORT_TOKEN"
   ```

   Refresh it before it expires:

   ```bash
   curl "https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token=LONG_TOKEN"
   ```

5. **App Review for `threads_keyword_search`.** Until Meta approves it, search only returns your
   own posts, so `discover` finds nothing to reply to. `outreach check` tells you which state
   you're in. For the review, record your screen running `check → discover → review → post`
   and describe the use honestly: you find public posts about postpartum life, draft replies,
   approve each one yourself, and post from your own account. Meta may also ask you to verify
   your identity or business.

### 2. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml   # then edit [voice] facts and style
cp .env.example .env                 # then fill in both keys
set -a; source .env; set +a
outreach check
```

`ANTHROPIC_API_KEY` comes from [platform.claude.com](https://platform.claude.com). If you've run
`ant auth login`, you can leave it empty.

### 3. Tell it about you

Edit `[voice]` in `config.toml`. `facts` is the only personal information the drafts may use;
the model is told never to invent anything else (no made-up kids, birth stories or feelings).
Leave it short and true.

## Daily use

```bash
outreach discover           # ~1-2 min; drafts up to 25 replies
outreach review             # a / e / l / s / q for each draft
outreach post --dry-run     # see exactly what will go out
outreach post               # posts, waiting 5 min between replies
outreach stats              # counts, skip reasons, link share, links to your replies
```

To run `post` unattended after you've reviewed, schedule `outreach post --max 1` every 15
minutes (cron or similar). It only ever posts what you approved.

**Tracking traffic:** every link carries `utm_source=threads&utm_medium=social&utm_campaign=replies`,
so visits show up under that campaign in your site analytics.

## Cost

Each post assessed is one Claude call (Claude Opus 5.5, medium effort), roughly 2–4¢.
25 drafts a day is about $1. Change `[model]` in `config.toml` to adjust.

## How it's built

```
outreach/
  threads_api.py   Threads Graph API: search, reply (create container -> publish), quota
  drafter.py       Claude: classify the post and draft a reply (structured output)
  policy.py        hard rules: who never gets a reply, link budget, validation, warnings
  pipeline.py      discover (search + draft) and post (publish approved)
  review.py        the interactive approval screen
  store.py         SQLite: one row per post, new -> drafted -> approved -> posted
  cli.py           the `outreach` command
```

Tests: `pytest` (no network; Threads and Claude are faked).

Not built yet: replying inside comment threads, DMs, original posts, other platforms.
