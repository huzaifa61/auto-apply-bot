# LinkedIn Easy Apply bot (manual-run, macOS)

Reads the "Jobs" Google Sheet your Apify actor already fills in, opens each
unprocessed job in your real Chrome, tries Easy Apply, fills recognized
questions from your answer bank, and **skips and logs anything it doesn't
recognize** instead of guessing. You review the flagged list afterward,
add the missing Q&A to `config/answer_bank.json`, and re-run.

## ⚠️ Before you use this

Automating applications on LinkedIn goes against their Terms of Service.
LinkedIn does detect and can restrict or ban accounts for bot-like
behavior. This script tries to reduce that risk (uses your real logged-in
Chrome profile, randomized delays between applications, capped batch
size), but it can't eliminate it. Use a sensible daily cap, and treat this
as "use at your own risk" for your account.

## 1. One-time setup

```bash
cd linkedin-apply-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chrome
```

Copy the example config files and fill them in:

```bash
cp .env.example .env
cp config/profile.example.json config/profile.json
cp config/answer_bank.example.json config/answer_bank.json
```

- `.env` → paste your Apps Script Web App URL (same one your Apify actor uses).
- `config/profile.json` → your real name, email, phone, path to your resume PDF on disk, etc.
- `config/answer_bank.json` → seed it with the common questions you already know LinkedIn asks. You'll add more here over time as jobs get flagged.

## 2. Update your Google Sheet's Apps Script

Open your Sheet → **Extensions → Apps Script**, replace the code with the
contents of `apps_script.gs` in this folder, then
**Deploy → Manage deployments → edit (pencil) → New version → Deploy**.
(Editing the existing deployment keeps the same URL, so you don't need to
change it anywhere else.) This adds:
- Two new columns: "Application Status" and "Flag Reason"
- A `?action=queue` endpoint the bot reads jobs from
- A status-update endpoint the bot writes results back to

## 3. Log into LinkedIn once, in the bot's own Chrome profile

The bot uses a dedicated Chrome profile (separate from your everyday one)
so it doesn't touch your normal browsing session. First time only:

```bash
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context('~/.linkedin-apply-bot-profile', channel='chrome', headless=False)
    page = ctx.pages[0]
    page.goto('https://www.linkedin.com/login')
    input('Log in, then press Enter here...')
    ctx.close()
"
```

## 4. Run it

```bash
# Rehearsal first — fills forms but never clicks the final Submit:
python apply_bot.py --max 5 --dry-run

# The real thing:
python apply_bot.py --max 15
```

Each run prints a summary and writes a full JSON report to `logs/`. Rows
in the Sheet get their **Application Status** set to `Applied`, `Flagged`,
or `Failed`, plus a **Flag Reason** where relevant, so the Sheet itself
becomes your daily "what happened" log.

## 5. Handling flagged jobs

Flagged jobs are **not** retried automatically (so you don't get spammed
with the same unanswered question every day). To retry one:
1. Look at the reason + screenshot in `logs/`.
2. Add a matching rule to `config/answer_bank.json`.
3. Clear the "Application Status" cell for that row in the Sheet.
4. Run the bot again — it'll pick it back up.

## Notes / known limitations

- LinkedIn changes its Easy Apply markup periodically — if the bot starts
  flagging everything at the same step, the selectors in `apply_bot.py`
  (`EASY_APPLY_SELECTORS`, `NEXT_SELECTORS`, etc.) likely need a small
  update to match the current page.
- Multi-select questions, "yes/no" toggles rendered as buttons rather than
  radios, and cover-letter text boxes aren't specifically handled yet —
  they'll get flagged rather than guessed at.
- This is a personal automation tool, not a production system — keep an
  eye on early runs.
# auto-apply-bot
